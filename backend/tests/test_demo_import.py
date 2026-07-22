import json
from pathlib import Path

import pytest
from app.db.base import Base
from app.models import BidProject, Requirement
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo_data"


@pytest.fixture()
def demo_db_url(monkeypatch):
    """Use the unified TEST_DATABASE_URL from conftest — never hardcode hosts."""
    from tests.conftest import POSTGRES_ERROR, POSTGRES_OK, TEST_DATABASE_URL

    if not POSTGRES_OK:
        pytest.fail(
            f"PostgreSQL required for demo import idempotent test but unreachable: {POSTGRES_ERROR}"
        )
    url = TEST_DATABASE_URL
    eng = create_engine(url, future=True)
    Base.metadata.drop_all(bind=eng)
    Base.metadata.create_all(bind=eng)
    eng.dispose()
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def test_demo_import_idempotent(demo_db_url):
    import importlib.util

    script = ROOT / "scripts" / "import_demo_data.py"
    spec = importlib.util.spec_from_file_location("import_demo_data", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    first = mod.import_demo(demo_root=DEMO, dry_run=False, database_url=demo_db_url)
    second = mod.import_demo(demo_root=DEMO, dry_run=False, database_url=demo_db_url)

    assert first["projects"]["created"] == 1
    assert first["requirements"]["created"] == 2
    assert second["projects"]["skipped"] == 1
    assert second["requirements"]["skipped"] == 2
    assert second["requirements"]["created"] == 0

    eng = create_engine(demo_db_url, future=True)
    SessionLocal = sessionmaker(bind=eng)
    with SessionLocal() as db:
        project = db.scalar(select(BidProject).where(BidProject.project_code == "DEMO-2026-001"))
        assert project is not None
        reqs = list(db.scalars(select(Requirement).where(Requirement.project_id == project.id)))
        assert len(reqs) == 2
        # original requirement ids preserved
        ids = {str(r.id) for r in reqs}
        assert "11111111-1111-1111-1111-111111111101" in ids
    eng.dispose()


def test_demo_dry_run_outputs_stats():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_demo_data.py"), "--dry-run"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
