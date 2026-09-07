"""Run only against the explicitly designated disposable integration database."""
import os
import pytest
from fastapi.testclient import TestClient

# Set URLs before importing application modules in integration tests.
if os.getenv("ROOMSCOUT_INTEGRATION") == "1":
    os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://roomscout:roomscout@localhost:55432/roomscout_test")
    os.environ["CHECKPOINT_URL"] = os.environ.get("TEST_CHECKPOINT_URL", "postgresql://roomscout:roomscout@localhost:55432/roomscout_test?options=-csearch_path%3Dcheckpoints")


@pytest.fixture(autouse=True)
def integration_enabled():
    if os.getenv("ROOMSCOUT_INTEGRATION") != "1":
        pytest.skip("Set ROOMSCOUT_INTEGRATION=1 and prepare the disposable test database")


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as client:
        yield client
