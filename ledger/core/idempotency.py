import hashlib

from django.db import transaction
from django.http import HttpResponse
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView, exception_handler

from core.exceptions import IdempotencyConflict
from core.models import IdempotencyRecord

MAX_KEY_LENGTH = 255


def _key_error(message: str) -> ValidationError:
    return ValidationError({"Idempotency-Key": [message]})


class IdempotentCreateView(APIView):
    """POST that is safe to repeat with the same Idempotency-Key.

    The record and the money movement commit in one database transaction. Any
    response produced after authentication is stored, errors included, so a
    repeat gets exactly the same status and bytes. A concurrent repeat waits on
    the unique index of IdempotencyRecord until the first request commits.
    """

    def perform(self, request, key: str) -> tuple[int, dict]:
        raise NotImplementedError

    def post(self, request):
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            raise _key_error("This header is required.")
        if len(key) > MAX_KEY_LENGTH:
            raise _key_error(f"Must be at most {MAX_KEY_LENGTH} characters.")

        # request.body before request.data: the hash covers the raw bytes.
        request_hash = hashlib.sha256(
            f"{request.method} {request.path}\n".encode() + request.body
        ).hexdigest()

        with transaction.atomic():
            record, created = IdempotencyRecord.objects.get_or_create(
                account=request.user,
                key=key,
                defaults={
                    "request_hash": request_hash,
                    "response_status": 0,
                    "response_body": "",
                },
            )
            if created:
                record.response_status, record.response_body = self._execute(request, key)
                record.save(update_fields=["response_status", "response_body"])
            elif record.request_hash != request_hash:
                raise IdempotencyConflict()

        return HttpResponse(
            record.response_body,
            status=record.response_status,
            content_type="application/json",
        )

    def _execute(self, request, key: str) -> tuple[int, str]:
        try:
            # Savepoint: a failed operation leaves no partial writes, the record stays.
            with transaction.atomic():
                status_code, body = self.perform(request, key)
        except APIException as exc:
            response = exception_handler(exc, self.get_exception_handler_context())
            status_code, body = response.status_code, response.data
        return status_code, JSONRenderer().render(body).decode()
