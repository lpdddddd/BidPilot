from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import *  # noqa: F403
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_PG = "postgresql+psycopg://bidpilot@127.0.0.1:5432/bidpilot_test"
TEST_DATABASE_URL = os.getenv("DATABASE_URL_TEST") or DEFAULT_PG
USE_POSTGRES = TEST_DATABASE_URL.startswith("postgresql")

ENUM_TYPE_NAMES = [
    "project_status",
    "member_role",
    "document_type",
    "parse_status",
    "agent_run_status",
    "message_role",
    "requirement_category",
    "risk_level",
    "quality_level",
    "review_status",
    "match_status",
]


def _postgres_reachable(url: str) -> bool:
    try:
        eng = create_engine(url, future=True, pool_pre_ping=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


POSTGRES_OK = USE_POSTGRES and _postgres_reachable(TEST_DATABASE_URL)


def _reset_schema(eng) -> None:
    Base.metadata.drop_all(bind=eng)
    with eng.begin() as conn:
        for name in ENUM_TYPE_NAMES:
            conn.execute(text(f'DROP TYPE IF EXISTS "{name}" CASCADE'))
    Base.metadata.create_all(bind=eng)


@pytest.fixture(autouse=True)
def _no_auto_indexing(monkeypatch):
    """Chunk builds trigger real indexing (Qdrant/OpenSearch/models); tests
    stub the trigger and exercise index tasks explicitly with fakes."""
    calls: list = []
    monkeypatch.setattr(
        "app.services.chunk_tasks._trigger_indexing", lambda document_id: calls.append(document_id)
    )
    return calls


@pytest.fixture()
def engine():
    if not POSTGRES_OK:
        pytest.skip("PostgreSQL is not available for integration tests")
    eng = create_engine(TEST_DATABASE_URL, future=True, pool_pre_ping=True)
    _reset_schema(eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    with eng.begin() as conn:
        for name in ENUM_TYPE_NAMES:
            conn.execute(text(f'DROP TYPE IF EXISTS "{name}" CASCADE'))
    eng.dispose()


@pytest.fixture()
def db(engine) -> Generator[Session, None, None]:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db: Session) -> Generator[TestClient, None, None]:
    def _override_get_db() -> Generator[Session, None, None]:
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def client_no_db() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
