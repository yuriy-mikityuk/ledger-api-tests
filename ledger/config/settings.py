import json
import os
from decimal import Decimal
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-secret")
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "core",
]

MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
    }
]

DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL", "postgres://ledger:ledger@localhost:5432/ledger")
    )
}

AUTH_USER_MODEL = "core.Account"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["core.parsers.DecimalJSONParser"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "COERCE_DECIMAL_TO_STRING": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Mini-ledger API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "plain",
        }
    },
    "root": {"handlers": ["stdout"], "level": "INFO"},
}

PROVIDER_WEBHOOK_SECRET = os.environ.get("PROVIDER_WEBHOOK_SECRET", "")
if not PROVIDER_WEBHOOK_SECRET:
    raise ImproperlyConfigured("PROVIDER_WEBHOOK_SECRET must be set")

TEST_SUPPORT_ENABLED = os.environ.get("TEST_SUPPORT_ENABLED") == "1"

PLATFORM_SPREAD = Decimal("0.005")

DEFAULT_EXCHANGE_RATES = {
    "USD_EUR": "0.92",
    "EUR_USD": "1.08",
    "USD_USDT": "1.00",
    "USDT_USD": "1.00",
    "EUR_USDT": "1.08",
    "USDT_EUR": "0.92",
}


def _load_exchange_rates() -> dict[str, Decimal]:
    raw = os.environ.get("EXCHANGE_RATES")
    # parse_float=Decimal: a rate written as a JSON number never becomes a float.
    source = json.loads(raw, parse_float=Decimal) if raw else DEFAULT_EXCHANGE_RATES
    return {pair: Decimal(str(rate)) for pair, rate in source.items()}


EXCHANGE_RATES = _load_exchange_rates()
