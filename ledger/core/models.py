from decimal import Decimal

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.money import Currency

MONEY = {"max_digits": 20, "decimal_places": 6}


class Account(AbstractBaseUser):
    """The API client. The DRF Token is reachable as account.auth_token."""

    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"

    objects = BaseUserManager()


class Wallet(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="wallets")
    currency = models.CharField(max_length=4, choices=Currency.choices)
    balance = models.DecimalField(**MONEY, default=Decimal("0"))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "currency"], name="uniq_wallet_per_currency")
        ]


class Transaction(models.Model):
    class Type(models.TextChoices):
        DEPOSIT = "deposit"
        WITHDRAWAL = "withdrawal"
        EXCHANGE_OUT = "exchange_out"
        EXCHANGE_IN = "exchange_in"

    class Status(models.TextChoices):
        PENDING = "pending"
        COMPLETED = "completed"
        FAILED = "failed"

    # Denormalized from wallet.account: the idempotency_key constraint needs it.
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="transactions")
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    type = models.CharField(max_length=12, choices=Type.choices)
    amount = models.DecimalField(**MONEY)
    fee = models.DecimalField(**MONEY, default=Decimal("0"))
    status = models.CharField(max_length=9, choices=Status.choices)
    provider_reference = models.UUIDField(unique=True, null=True, blank=True)
    # An exchange has two legs and one key: the key is stored on exchange_out only.
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account", "idempotency_key"], name="uniq_transaction_idempotency_key"
            )
        ]


class Quote(models.Model):
    from_wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="quotes_from")
    to_wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="quotes_to")
    amount_from = models.DecimalField(**MONEY)
    amount_to = models.DecimalField(**MONEY)
    rate = models.DecimalField(max_digits=20, decimal_places=10)
    spread = models.DecimalField(max_digits=6, decimal_places=4)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)


class WebhookEvent(models.Model):
    event_id = models.CharField(max_length=255, unique=True)
    payload = models.JSONField(encoder=DjangoJSONEncoder)
    received_at = models.DateTimeField(auto_now_add=True)


class IdempotencyRecord(models.Model):
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="idempotency_records"
    )
    key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    # Rendered JSON as text: jsonb would reorder keys and the replay must be identical.
    response_body = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "key"], name="uniq_idempotency_record")
        ]
