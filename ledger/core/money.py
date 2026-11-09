"""All money arithmetic of the service.

Only Decimal is used here and float is forbidden everywhere in the service:
amounts arrive as JSON strings, JSON numbers are parsed to Decimal
(core/parsers.py) and rejected by MoneyField (core/serializers.py).
"""

from decimal import ROUND_HALF_UP, Context, Decimal

from django.db import models


class Currency(models.TextChoices):
    USD = "USD"
    EUR = "EUR"
    USDT = "USDT"


# Explicit context: the default one keeps 28 digits, which an amount of
# 20 digits multiplied by a rate can exceed.
MONEY_CONTEXT = Context(prec=60, rounding=ROUND_HALF_UP)

SCALES = {Currency.USD: 2, Currency.EUR: 2, Currency.USDT: 6}

MIN_AMOUNT = Decimal("10")
MAX_AMOUNT = Decimal("100000")
# What fits into DecimalField(max_digits=20, decimal_places=6).
MAX_STORABLE_AMOUNT = Decimal("99999999999999.999999")

FIAT_FEE_RATE = Decimal("0.005")
FIAT_MIN_FEE = Decimal("1.00")
USDT_WITHDRAWAL_FEE = Decimal("1.000000")

RATE_QUANTUM = Decimal("0.0000000001")


def quantum(currency: str) -> Decimal:
    return Decimal(1).scaleb(-SCALES[currency])


def round_money(value: Decimal, currency: str) -> Decimal:
    return value.quantize(quantum(currency), context=MONEY_CONTEXT)


def format_money(value: Decimal, currency: str) -> str:
    return format(round_money(value, currency), "f")


def fits_currency_scale(amount: Decimal, currency: str) -> bool:
    return -amount.as_tuple().exponent <= SCALES[currency]


def withdrawal_fee(amount: Decimal, currency: str) -> Decimal:
    if currency == Currency.USDT:
        return USDT_WITHDRAWAL_FEE
    percent_fee = round_money(MONEY_CONTEXT.multiply(amount, FIAT_FEE_RATE), currency)
    return max(percent_fee, FIAT_MIN_FEE)


def rate_with_spread(base_rate: Decimal, spread: Decimal) -> Decimal:
    return MONEY_CONTEXT.multiply(base_rate, 1 - spread).quantize(
        RATE_QUANTUM, context=MONEY_CONTEXT
    )


def convert(amount: Decimal, rate: Decimal, target_currency: str) -> Decimal:
    return round_money(MONEY_CONTEXT.multiply(amount, rate), target_currency)
