import hashlib
import hmac

from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import NotFound, ParseError, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core import serializers as s
from core import services
from core.exceptions import InvalidWebhookSignature
from core.idempotency import IdempotentCreateView
from core.models import Account, Transaction
from core.parsers import loads_without_float

ERROR = OpenApiTypes.OBJECT

IDEMPOTENCY_KEY_HEADER = OpenApiParameter(
    "Idempotency-Key", str, OpenApiParameter.HEADER, required=True
)
SIGNATURE_HEADER = OpenApiParameter(
    "X-Provider-Signature",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="sha256=<hex HMAC-SHA256 of the raw request body>",
)


class AccountCreateView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        request=s.AccountCreateSerializer,
        responses={201: s.AccountSerializer, 400: ERROR},
    )
    def post(self, request):
        serializer = s.AccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = services.create_account(serializer.validated_data["email"])
        return Response(s.AccountSerializer(account).data, status=status.HTTP_201_CREATED)


class AccountDeleteView(APIView):
    def initial(self, request, *args, **kwargs):
        # Before authentication: with the flag off the endpoint must not exist for anyone.
        if not settings.TEST_SUPPORT_ENABLED:
            raise NotFound()
        super().initial(request, *args, **kwargs)

    @extend_schema(responses={204: None, 401: ERROR, 403: ERROR, 404: ERROR})
    def delete(self, request, pk):
        if not Account.objects.filter(pk=pk).exists():
            raise NotFound("Account not found.")
        if pk != request.user.pk:
            raise PermissionDenied("Account belongs to another client.")
        services.delete_account(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WalletListView(APIView):
    @extend_schema(responses={200: s.WalletSerializer(many=True), 401: ERROR})
    def get(self, request):
        wallets = request.user.wallets.order_by("id")
        return Response(s.WalletSerializer(wallets, many=True).data)


class TransactionListView(APIView):
    @extend_schema(
        parameters=[s.TransactionListQuerySerializer],
        responses={
            200: s.TransactionSerializer(many=True),
            400: ERROR,
            401: ERROR,
            403: ERROR,
            404: ERROR,
        },
    )
    def get(self, request):
        query = s.TransactionListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        transactions = Transaction.objects.filter(account=request.user).select_related("wallet")
        wallet_id = query.validated_data.get("wallet_id")
        if wallet_id is not None:
            wallet = services.get_owned_wallet(request.user, wallet_id)
            transactions = transactions.filter(wallet=wallet)
        transactions = transactions.order_by("-created_at", "-id")
        return Response(s.TransactionSerializer(transactions, many=True).data)


@extend_schema_view(
    post=extend_schema(
        parameters=[IDEMPOTENCY_KEY_HEADER],
        request=s.DepositCreateSerializer,
        responses={
            201: s.TransactionSerializer,
            400: ERROR,
            401: ERROR,
            403: ERROR,
            404: ERROR,
            422: ERROR,
        },
    )
)
class DepositCreateView(IdempotentCreateView):
    def perform(self, request, key):
        serializer = s.DepositCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        tx = services.create_deposit(
            request.user,
            serializer.validated_data["wallet"],
            serializer.validated_data["amount"],
            key,
        )
        return status.HTTP_201_CREATED, s.TransactionSerializer(tx).data


@extend_schema_view(
    post=extend_schema(
        parameters=[IDEMPOTENCY_KEY_HEADER],
        request=s.WithdrawalCreateSerializer,
        responses={
            201: s.TransactionSerializer,
            400: ERROR,
            401: ERROR,
            403: ERROR,
            404: ERROR,
            422: ERROR,
        },
    )
)
class WithdrawalCreateView(IdempotentCreateView):
    def perform(self, request, key):
        serializer = s.WithdrawalCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        tx = services.create_withdrawal(
            request.user,
            serializer.validated_data["wallet"].pk,
            serializer.validated_data["amount"],
            key,
        )
        return status.HTTP_201_CREATED, s.TransactionSerializer(tx).data


class QuoteCreateView(APIView):
    @extend_schema(
        request=s.QuoteCreateSerializer,
        responses={201: s.QuoteSerializer, 400: ERROR, 401: ERROR, 403: ERROR, 404: ERROR},
    )
    def post(self, request):
        serializer = s.QuoteCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        quote = services.create_quote(
            serializer.validated_data["from_wallet"],
            serializer.validated_data["to_wallet"],
            serializer.validated_data["amount"],
        )
        return Response(s.QuoteSerializer(quote).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    post=extend_schema(
        parameters=[IDEMPOTENCY_KEY_HEADER],
        request=s.ExchangeCreateSerializer,
        responses={
            201: s.ExchangeResultSerializer,
            400: ERROR,
            401: ERROR,
            403: ERROR,
            404: ERROR,
            409: ERROR,
            422: ERROR,
        },
    )
)
class ExchangeCreateView(IdempotentCreateView):
    def perform(self, request, key):
        serializer = s.ExchangeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quote, out_leg, in_leg = services.execute_exchange(
            request.user, serializer.validated_data["quote_id"], key
        )
        result = {"quote_id": quote.id, "exchange_out": out_leg, "exchange_in": in_leg}
        return status.HTTP_201_CREATED, s.ExchangeResultSerializer(result).data


def _verify_signature(raw_body: bytes, header: str) -> None:
    digest = hmac.new(
        settings.PROVIDER_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    # Bytes: compare_digest raises TypeError on a non-ASCII str.
    if not hmac.compare_digest(f"sha256={digest}".encode(), header.encode()):
        raise InvalidWebhookSignature()


class ProviderWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        parameters=[SIGNATURE_HEADER],
        request=s.ProviderWebhookSerializer,
        responses={
            200: s.WebhookAckSerializer,
            400: ERROR,
            401: ERROR,
            404: ERROR,
            409: ERROR,
        },
    )
    def post(self, request):
        raw_body = request.body
        _verify_signature(raw_body, request.headers.get("X-Provider-Signature", ""))
        try:
            payload = loads_without_float(raw_body)
        except ValueError as exc:
            raise ParseError(f"JSON parse error - {exc}")
        serializer = s.ProviderWebhookSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.process_provider_webhook(
            data["event_id"], data["event"], data["provider_reference"], data["amount"], payload
        )
        return Response({"status": "ok"})
