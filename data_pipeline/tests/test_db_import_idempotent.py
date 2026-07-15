import os
import sys
import uuid
from pathlib import Path

import pytest

from bidpilot_data.database.importers import import_company_profiles
from bidpilot_data.settings import Settings, get_settings, override_settings
from bidpilot_data.utils import write_jsonl


@pytest.mark.skipif(
    os.getenv("SKIP_DB_TESTS", "0") == "1",
    reason="DB tests skipped",
)
def test_company_import_idempotent(tmp_repo, tmp_datasets):
    url = os.getenv("DATABASE_URL_TEST", "postgresql+psycopg://bidpilot@127.0.0.1:5432/bidpilot_test")
    try:
        from sqlalchemy import create_engine, text

        eng = create_engine(url, future=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception:
        pytest.skip("PostgreSQL unavailable")

    override_settings(Settings(repo_root=tmp_repo, database_url=url))

    company_id = str(uuid.uuid4())
    write_jsonl(
        tmp_datasets / "silver" / "company_profiles.jsonl",
        [
            {
                "company_profile_id": company_id,
                "name": f"公开披露测试供应商-{company_id[:8]}",
                "credit_code": None,
                "industry": "信息化",
                "synthetic": False,
                "metadata": {"disclosed_only": True},
            }
        ],
    )

    backend = Path(__file__).resolve().parents[2] / "backend"
    sys.path.insert(0, str(backend))
    from app.db.base import Base
    import app.models  # noqa: F401
    from sqlalchemy import create_engine

    eng = create_engine(url, future=True)
    Base.metadata.create_all(bind=eng)
    eng.dispose()

    assert get_settings().database_url == url
    s1 = import_company_profiles()
    s2 = import_company_profiles()
    assert s1["created"] >= 1
    assert s2["skipped"] >= 1
    assert s2["created"] == 0
