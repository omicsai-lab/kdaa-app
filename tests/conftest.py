import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from kdaa.core.service import KDAAService
from kdaa.providers.local import LocalAssetStore
from kdaa_api.main import create_app
from .support import MemoryStore

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def store():
    return MemoryStore()
@pytest.fixture
def core(tmp_path, store):
    return KDAAService(store, LocalAssetStore(tmp_path / "assets"), demo_path=ROOT / "data/demo")
@pytest.fixture
def client(core):
    with TestClient(create_app(core), raise_server_exceptions=False) as client:
        yield client

def pytest_sessionstart(session):
    if os.getenv("KDAA_REQUIRE_POSTGRES_TESTS") == "1" and not os.getenv("KDAA_TEST_DATABASE_URL"):
        raise pytest.UsageError("KDAA_REQUIRE_POSTGRES_TESTS=1 requires KDAA_TEST_DATABASE_URL; do not silently skip PostgreSQL.")
