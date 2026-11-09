import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from core import money
from core.exceptions import Conflict
from core.models import Account, Quote, Transaction, Wallet, WebhookEvent
from core.money import Currency, format_money

logger = logging.getLogger("ledger.money")

QUOTE_TTL = timedelta(seconds=30)

EVENT_TRANSACTION_TYPE = {
    "deposit.completed": Transaction.Type.DEPOSIT,
    "withdrawal.completed": Transaction.Type.WITHDRAWAL,
    "withdrawal.failed": Transaction.Type.WITHDRAWAL,
}


def _log_money_operation(operation: str, tx: Transaction) -> None:
    currency = tx.wallet.currency
    line = (
        f"money_operation op={operation} tx_id={tx.id} wallet_id={tx.wallet_id} "
        f"currency={currency} amount={format_money(tx.amount, currency)} "
        f"fee={format_money(tx.fee, currency)} status={tx.status}"
    )
    # After commit: a rolled back operation must not leave a log line.
    transaction.on_commit(lambda: logger.info(line))


def _wallets_for_update():
    # no_key: plain FOR UPDATE also conflicts with the FOR KEY SHARE taken by FK checks
    # of concurrent inserts into core_quote, and that cycle deadlocks with an exchange.
    return Wallet.objects.select_for_update(no_key=True)


def get_owned_wallet(account: Account, wallet_id: int) -> Wallet:
    wallet = Wallet.objects.filter(pk=wallet_id).first()
    if wallet is None:
        raise NotFound("Wallet not found.")
    if wallet.account_id != account.id:
        raise PermissionDenied("Wallet belongs to another account.")
    return wallet


def create_account(email: str) -> Account:
    try:
        with transaction.atomic():
            account = Account(email=email)
            account.set_unusable_password()
            account.save()
            Token.objects.create(user=account)
            Wallet.objects.bulk_create(
                Wallet(account=account, currency=currency) for currency in Currency.values
            )
    except IntegrityError:
        raise ValidationError({"email": ["Account with this email already exists."]})
    return account


@transaction.atomic
def delete_account(account: Account) -> None:
    references = Transaction.objects.filter(
        account=account, provider_reference__isnull=False
    ).values_list("provider_reference", flat=True)
    WebhookEvent.objects.filter(
        payload__provider_reference__in=[str(reference) for reference in references]
    ).delete()
    account.delete()


@transaction.atomic
def create_deposit(account: Account, wallet: Wallet, amount: Decimal, key: str) -> Transaction:
    tx = Transaction.objects.create(
        account=account,
        wallet=wallet,
        type=Transaction.Type.DEPOSIT,
        amount=amount,
        status=Transaction.Status.PENDING,
        provider_reference=uuid.uuid4(),
        idempotency_key=key,
    )
    _log_money_operation("deposit", tx)
    return tx


@transaction.atomic
def create_withdrawal(account: Account, wallet_id: int, amount: Decimal, key: str) -> Transaction:
    wallet = _wallets_for_update().get(pk=wallet_id)
    fee = money.withdrawal_fee(amount, wallet.currency)
    total = amount + fee
    if wallet.balance < total:
        raise ValidationError(
            {
                "amount": [
                    f"Insufficient funds: available {format_money(wallet.balance, wallet.currency)}"
                    f" {wallet.currency}, required {format_money(total, wallet.currency)}"
                    f" including fee {format_money(fee, wallet.currency)}."
                ]
            }
        )
    wallet.balance -= total
    wallet.save(update_fields=["balance"])
    tx = Transaction.objects.create(
        account=account,
        wallet=wallet,
        type=Transaction.Type.WITHDRAWAL,
        amount=amount,
        fee=fee,
        status=Transaction.Status.PENDING,
        provider_reference=uuid.uuid4(),
        idempotency_key=key,
    )
    _log_money_operation("withdrawal", tx)
    return tx


def create_quote(from_wallet: Wallet, to_wallet: Wallet, amount: Decimal) -> Quote:
    base_rate = settings.EXCHANGE_RATES.get(f"{from_wallet.currency}_{to_wallet.currency}")
    if base_rate is None:
        raise ValidationError({"to_wallet_id": ["No exchange rate for this currency pair."]})
    rate = money.rate_with_spread(base_rate, settings.PLATFORM_SPREAD)
    amount_to = money.convert(amount, rate, to_wallet.currency)
    if amount_to <= 0:
        raise ValidationError({"amount": ["Amount is too small to exchange."]})
    if amount_to > money.MAX_STORABLE_AMOUNT:
        raise ValidationError({"amount": ["Amount is too large."]})
    return Quote.objects.create(
        from_wallet=from_wallet,
        to_wallet=to_wallet,
        amount_from=amount,
        amount_to=amount_to,
        rate=rate,
        spread=settings.PLATFORM_SPREAD,
        expires_at=timezone.now() + QUOTE_TTL,
    )


@transaction.atomic
def execute_exchange(
    account: Account, quote_id: int, key: str
) -> tuple[Quote, Transaction, Transaction]:
    quote = Quote.objects.select_for_update().filter(pk=quote_id).first()
    if quote is None:
        raise NotFound("Quote not found.")

    # Fixed lock order: two opposite exchanges must not deadlock.
    locked = _wallets_for_update().filter(pk__in=[quote.from_wallet_id, quote.to_wallet_id])
    wallets = {wallet.pk: wallet for wallet in locked.order_by("pk")}
    from_wallet = wallets[quote.from_wallet_id]
    to_wallet = wallets[quote.to_wallet_id]

    if from_wallet.account_id != account.id:
        raise PermissionDenied("Quote belongs to another account.")
    if quote.used:
        raise Conflict("Quote was already used.")
    if timezone.now() >= quote.expires_at:
        raise Conflict("Quote has expired.")
    if from_wallet.balance < quote.amount_from:
        raise ValidationError(
            {
                "non_field_errors": [
                    f"Insufficient funds: available "
                    f"{format_money(from_wallet.balance, from_wallet.currency)}"
                    f" {from_wallet.currency}."
                ]
            }
        )

    from_wallet.balance -= quote.amount_from
    to_wallet.balance += quote.amount_to
    from_wallet.save(update_fields=["balance"])
    to_wallet.save(update_fields=["balance"])
    quote.used = True
    quote.save(update_fields=["used"])

    out_leg = Transaction.objects.create(
        account=account,
        wallet=from_wallet,
        type=Transaction.Type.EXCHANGE_OUT,
        amount=quote.amount_from,
        status=Transaction.Status.COMPLETED,
        idempotency_key=key,
    )
    in_leg = Transaction.objects.create(
        account=account,
        wallet=to_wallet,
        type=Transaction.Type.EXCHANGE_IN,
        amount=quote.amount_to,
        status=Transaction.Status.COMPLETED,
    )
    _log_money_operation("exchange_out", out_leg)
    _log_money_operation("exchange_in", in_leg)
    return quote, out_leg, in_leg


def _locked_transaction_by_reference(reference: str) -> Transaction:
    try:
        parsed = uuid.UUID(reference)
    except ValueError:
        raise NotFound("Unknown provider_reference.")
    tx = Transaction.objects.select_for_update().filter(provider_reference=parsed).first()
    if tx is None:
        raise NotFound("Unknown provider_reference.")
    return tx


@transaction.atomic
def process_provider_webhook(
    event_id: str, event: str, reference: str, amount: Decimal, payload: dict
) -> None:
    # Blocks on the unique index while a concurrent delivery of this event is in
    # flight. A 404 or 409 below rolls this row back, so the event can be retried.
    _, created = WebhookEvent.objects.get_or_create(
        event_id=event_id, defaults={"payload": payload}
    )
    if not created:
        logger.info("webhook_duplicate event_id=%s", event_id)
        return

    tx = _locked_transaction_by_reference(reference)
    if tx.type != EVENT_TRANSACTION_TYPE[event]:
        raise Conflict(f"Event {event} does not apply to a {tx.type} transaction.")
    if tx.status != Transaction.Status.PENDING:
        raise Conflict(f"Transaction is already {tx.status}.")

    wallet = _wallets_for_update().get(pk=tx.wallet_id)
    if event == "deposit.completed":
        if amount == tx.amount:
            wallet.balance += amount
            wallet.save(update_fields=["balance"])
            tx.status = Transaction.Status.COMPLETED
        else:
            tx.status = Transaction.Status.FAILED
    elif event == "withdrawal.completed":
        tx.status = Transaction.Status.COMPLETED
    else:
        wallet.balance += tx.amount + tx.fee
        wallet.save(update_fields=["balance"])
        tx.status = Transaction.Status.FAILED

    tx.save(update_fields=["status", "updated_at"])
    _log_money_operation(event, tx)
