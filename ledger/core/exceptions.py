from rest_framework.exceptions import APIException


class Conflict(APIException):
    status_code = 409
    default_detail = "Conflict with the current state."
    default_code = "conflict"


class IdempotencyConflict(APIException):
    status_code = 422
    default_detail = "Idempotency-Key was already used with a different request."
    default_code = "idempotency_conflict"


class InvalidWebhookSignature(APIException):
    # Not AuthenticationFailed: on a view without authenticators DRF turns that into 403.
    status_code = 401
    default_detail = "Invalid webhook signature."
    default_code = "invalid_signature"
