import pytest
import os
from helpers.client import BaseUrlSession

@pytest.fixture(scope="session")
def settings():
    return {
        "ledger_base_url": os.environ["LEDGER_BASE_URL"],
        "ledger_db_dsn": os.environ["LEDGER_DB_DSN"],
        "provider_webhook_secret": os.environ["PROVIDER_WEBHOOK_SECRET"],
    }

@pytest.fixture(scope="session")
def api(settings):
    s = BaseUrlSession(settings["ledger_base_url"])
    yield s
    s.close()



