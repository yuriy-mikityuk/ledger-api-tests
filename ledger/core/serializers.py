import re
from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core import money
from core.models import Account, Quote, Transaction, Wallet
from core.money import Currency, format_money
from core.services import get_owned_wallet

AMOUNT_PATTERN = re.compile(r"-?[0-9]+(\.[0-9]+)?")
BIGINT_MAX = 2**63 - 1

MONEY_SCHEMA = {
    "type": "string",
    "format": "decimal",
    "pattern": r"^-?[0-9]+(\.[0-9]+)?$",
    "example": "10.00",
}


@extend_schema_field(MONEY_SCHEMA)
class MoneyField(serializers.Field):
    """Accepts only a plain decimal string. JSON numbers are refused, nothing is rounded."""

    default_error_messages = {"invalid": 'Enter the amount as a decimal string, like "10.00".'}

    def to_internal_value(self, data) -> Decimal:
        if not isinstance(data, str) or not AMOUNT_PATTERN.fullmatch(data):
            self.fail("invalid")
        return Decimal(data)

    def to_representation(self, value) -> str:
        return format(value, "f")


def _amount_error(message: str) -> serializers.ValidationError:
    return serializers.ValidationError({"amount": [message]})


def _require_currency_scale(amount: Decimal, currency: str) -> None:
    if not money.fits_currency_scale(amount, currency):
        raise _amount_error(f"{currency} allows at most {money.SCALES[currency]} decimal places.")


def _require_operation_limits(amount: Decimal, currency: str) -> None:
    _require_currency_scale(amount, currency)
    if amount < money.MIN_AMOUNT:
        raise _amount_error(f"Minimum amount is {format_money(money.MIN_AMOUNT, currency)}.")
    if amount > money.MAX_AMOUNT:
        raise _amount_error(f"Maximum amount is {format_money(money.MAX_AMOUNT, currency)}.")


def _wallet_id_field() -> serializers.IntegerField:
    return serializers.IntegerField(min_value=1, max_value=BIGINT_MAX)


# Input


class AccountCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class DepositCreateSerializer(serializers.Serializer):
    wallet_id = _wallet_id_field()
    amount = MoneyField()

    def validate(self, attrs):
        wallet = get_owned_wallet(self.context["request"].user, attrs["wallet_id"])
        _require_operation_limits(attrs["amount"], wallet.currency)
        attrs["wallet"] = wallet
        return attrs


class WithdrawalCreateSerializer(DepositCreateSerializer):
    address = serializers.CharField()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        address = attrs["address"]
        if attrs["wallet"].currency == Currency.USDT and not (
            address.startswith("T") and len(address) == 34
        ):
            raise serializers.ValidationError(
                {"address": ["USDT address must start with T and be 34 characters long."]}
            )
        return attrs


class QuoteCreateSerializer(serializers.Serializer):
    from_wallet_id = _wallet_id_field()
    to_wallet_id = _wallet_id_field()
    amount = MoneyField()

    def validate(self, attrs):
        account = self.context["request"].user
        from_wallet = get_owned_wallet(account, attrs["from_wallet_id"])
        to_wallet = get_owned_wallet(account, attrs["to_wallet_id"])
        if from_wallet.currency == to_wallet.currency:
            raise serializers.ValidationError(
                {"to_wallet_id": ["Wallets must have different currencies."]}
            )
        _require_currency_scale(attrs["amount"], from_wallet.currency)
        if attrs["amount"] <= 0:
            raise _amount_error("Amount must be greater than zero.")
        if attrs["amount"] > money.MAX_STORABLE_AMOUNT:
            raise _amount_error("Amount is too large.")
        attrs["from_wallet"] = from_wallet
        attrs["to_wallet"] = to_wallet
        return attrs


class ExchangeCreateSerializer(serializers.Serializer):
    quote_id = serializers.IntegerField(min_value=1, max_value=BIGINT_MAX)


class TransactionListQuerySerializer(serializers.Serializer):
    wallet_id = serializers.IntegerField(required=False, min_value=1, max_value=BIGINT_MAX)


class ProviderWebhookSerializer(serializers.Serializer):
    event_id = serializers.CharField(max_length=255)
    event = serializers.ChoiceField(
        choices=["deposit.completed", "withdrawal.completed", "withdrawal.failed"]
    )
    # Not UUIDField: a malformed reference is an unknown reference, so 404 and not 400.
    provider_reference = serializers.CharField()
    amount = MoneyField()


# Output


class WalletSerializer(serializers.ModelSerializer):
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = ["id", "currency", "balance"]

    @extend_schema_field(MONEY_SCHEMA)
    def get_balance(self, wallet) -> str:
        return format_money(wallet.balance, wallet.currency)


class AccountSerializer(serializers.ModelSerializer):
    token = serializers.CharField(source="auth_token.key", read_only=True)
    wallets = WalletSerializer(many=True, read_only=True)

    class Meta:
        model = Account
        fields = ["id", "email", "token", "created_at", "wallets"]


class TransactionSerializer(serializers.ModelSerializer):
    wallet_id = serializers.IntegerField(read_only=True)
    amount = serializers.SerializerMethodField()
    fee = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            "id",
            "wallet_id",
            "type",
            "amount",
            "fee",
            "status",
            "provider_reference",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(MONEY_SCHEMA)
    def get_amount(self, tx) -> str:
        return format_money(tx.amount, tx.wallet.currency)

    @extend_schema_field(MONEY_SCHEMA)
    def get_fee(self, tx) -> str:
        return format_money(tx.fee, tx.wallet.currency)


class QuoteSerializer(serializers.ModelSerializer):
    from_wallet_id = serializers.IntegerField(read_only=True)
    to_wallet_id = serializers.IntegerField(read_only=True)
    amount_from = serializers.SerializerMethodField()
    amount_to = serializers.SerializerMethodField()
    rate = serializers.SerializerMethodField()
    spread = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = [
            "id",
            "from_wallet_id",
            "to_wallet_id",
            "amount_from",
            "amount_to",
            "rate",
            "spread",
            "expires_at",
            "used",
        ]

    @extend_schema_field(MONEY_SCHEMA)
    def get_amount_from(self, quote) -> str:
        return format_money(quote.amount_from, quote.from_wallet.currency)

    @extend_schema_field(MONEY_SCHEMA)
    def get_amount_to(self, quote) -> str:
        return format_money(quote.amount_to, quote.to_wallet.currency)

    @extend_schema_field(MONEY_SCHEMA)
    def get_rate(self, quote) -> str:
        return format(quote.rate, "f")

    @extend_schema_field(MONEY_SCHEMA)
    def get_spread(self, quote) -> str:
        return format(quote.spread, "f")


class ExchangeResultSerializer(serializers.Serializer):
    quote_id = serializers.IntegerField()
    exchange_out = TransactionSerializer()
    exchange_in = TransactionSerializer()


class WebhookAckSerializer(serializers.Serializer):
    status = serializers.CharField()
