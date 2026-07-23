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
from sqlalchemy.pool import NullPool

# Unified PostgreSQL test entry. Prefer TEST_DATABASE_URL; fall back to
# DATABASE_URL_TEST for backward compatibility. Never point at prod/dev DBs.
DEFAULT_PG = "postgresql+psycopg://bidpilot@127.0.0.1:5432/bidpilot_test"
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL_TEST") or DEFAULT_PG
USE_POSTGRES = TEST_DATABASE_URL.startswith("postgresql")

# Allow deterministic_fake in pytest / CI evaluation paths.
os.environ.setdefault("EVALUATION_ALLOW_FAKE", "1")
os.environ.setdefault("APP_ENV", "test")

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
    "extraction_run_status",
    "evidence_match_status",
    "match_review_status",
    "match_review_action",
    "match_review_reason_code",
    "actor_authn",
    "proposal_draft_status",
    "proposal_draft_version_kind",
    "proposal_draft_source_role",
    "proposal_draft_review_action",
    "proposal_draft_generation_mode",
    "compliance_severity",
    "compliance_finding_status",
    "compliance_rule_category",
    "evaluation_run_status",
    "evaluation_case_status",
    "evaluation_target_type",
    "evaluation_reference_kind",
]


def _assert_safe_test_database(url: str) -> None:
    """Refuse to run destructive schema resets against non-test databases."""
    lowered = url.lower()
    if "bidpilot_test" in lowered or "/test" in lowered or "_test" in lowered:
        return
    if os.getenv("BIDPILOT_ALLOW_NONTEST_DB") == "1":
        return
    raise RuntimeError(
        "Refusing to use a non-test database URL for pytest. "
        "Set TEST_DATABASE_URL to a dedicated test database "
        "(name must contain '_test'), or set BIDPILOT_ALLOW_NONTEST_DB=1 "
        "only for an isolated ephemeral instance. "
        f"Got: {url.split('@')[-1] if '@' in url else url}"
    )


def _postgres_reachable(url: str) -> tuple[bool, str | None]:
    try:
        eng = create_engine(url, future=True, pool_pre_ping=True, poolclass=NullPool)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


_assert_safe_test_database(TEST_DATABASE_URL)
POSTGRES_OK, POSTGRES_ERROR = (
    _postgres_reachable(TEST_DATABASE_URL) if USE_POSTGRES else (False, "not a postgresql URL")
)


def _reset_schema(eng) -> None:
    # Drop any leftover pooled connections before CASCADE so create_all is reliable
    eng.pool.dispose()
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
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
def engine(monkeypatch):
    if not POSTGRES_OK:
        pytest.fail(
            "PostgreSQL is required for integration tests but is unreachable.\n"
            f"  TEST_DATABASE_URL={TEST_DATABASE_URL}\n"
            f"  error={POSTGRES_ERROR}\n"
            "Start the test database, e.g.:\n"
            "  docker compose -f infra/docker-compose.test.yml up -d\n"
            "  export TEST_DATABASE_URL="
            "'postgresql+psycopg://bidpilot:bidpilot_test@127.0.0.1:5433/bidpilot_test'\n"
            "  cd backend && alembic upgrade head && pytest"
        )
    eng = create_engine(
        TEST_DATABASE_URL,
        future=True,
        pool_pre_ping=True,
        poolclass=NullPool,
    )
    _reset_schema(eng)
    TestSession = sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)
    # SSE / background agent & evaluation tasks must use the same test DB.
    monkeypatch.setattr("app.services.agent_run.sse.SESSION_FACTORY", TestSession)
    monkeypatch.setattr("app.services.agent_run.tasks.SESSION_FACTORY", TestSession)
    monkeypatch.setattr("app.services.evaluation.tasks.SESSION_FACTORY", TestSession)
    yield eng
    eng.pool.dispose()
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
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
