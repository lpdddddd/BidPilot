#!/usr/bin/env python3
"""Import bidpilot_demo_pack / demo_data into PostgreSQL without mutating source files.

Idempotent: re-runs skip existing rows using natural/business keys and metadata markers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_URL

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    BidProject,
    CompanyProfile,
    Organization,
    Requirement,
    RequirementMatch,
)
from app.models.enums import (
    MatchStatus,
    ProjectStatus,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)

DEMO_NAMESPACE = NAMESPACE_URL


def stable_uuid(key: str) -> UUID:
    return uuid5(DEMO_NAMESPACE, f"bidpilot-demo:{key}")


def find_demo_root(explicit: str | None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_path = os.getenv("DEMO_DATA_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            ROOT / "demo_data",
            ROOT / "bidpilot_demo_pack",
            ROOT.parent / "bidpilot_demo_pack",
            Path.cwd() / "bidpilot_demo_pack",
            Path.cwd() / "demo_data",
        ]
    )
    for path in candidates:
        if path.is_dir() and (
            (path / "project_info.json").exists()
            or (path / "requirements.json").exists()
            or list(path.glob("**/project_info.json"))
        ):
            return path
    # Nested pack layouts
    for path in candidates:
        if not path.is_dir():
            continue
        hits = list(path.rglob("project_info.json"))
        if hits:
            return hits[0].parent
    return None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def as_list(data: Any) -> list[Any]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "data", "records", "requirements", "matches", "companies"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return [data]
    return []


def parse_category(value: Any) -> RequirementCategory:
    if value is None:
        return RequirementCategory.project_info
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return RequirementCategory(text)
    except ValueError:
        mapping = {
            "qualification_requirement": RequirementCategory.qualification,
            "tech": RequirementCategory.technical,
            "technical_requirement": RequirementCategory.technical,
            "商务": RequirementCategory.commercial,
            "资质": RequirementCategory.qualification,
            "评分": RequirementCategory.scoring,
        }
        return mapping.get(text, RequirementCategory.project_info)


def parse_enum(enum_cls: type, value: Any, default: Any) -> Any:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return enum_cls(text)
    except ValueError:
        return default


def import_demo(*, demo_root: Path, dry_run: bool, database_url: str) -> dict[str, Any]:
    stats = {
        "demo_root": str(demo_root),
        "dry_run": dry_run,
        "organizations": {"created": 0, "skipped": 0},
        "projects": {"created": 0, "skipped": 0},
        "requirements": {"created": 0, "skipped": 0},
        "company_profiles": {"created": 0, "skipped": 0},
        "requirement_matches": {"created": 0, "skipped": 0},
    }

    project_info_path = demo_root / "project_info.json"
    requirements_path = demo_root / "requirements.json"
    matches_path = demo_root / "qualification_matches.json"

    company_candidates = [
        demo_root / "company_profiles.json",
        demo_root / "companies.json",
        demo_root / "virtual_companies.json",
        demo_root / "synthetic_companies.json",
    ]
    company_path = next((p for p in company_candidates if p.exists()), None)

    project_info = load_json(project_info_path) if project_info_path.exists() else {}
    requirements = as_list(load_json(requirements_path) if requirements_path.exists() else [])
    matches = as_list(load_json(matches_path) if matches_path.exists() else [])
    companies = as_list(load_json(company_path) if company_path else [])

    # Fallback: companies embedded in matches
    if not companies:
        seen: set[str] = set()
        for match in matches:
            name = match.get("company_name") or match.get("company") or match.get("bidder")
            if name and name not in seen:
                companies.append({"name": name, "synthetic": True})
                seen.add(name)

    org_name = os.getenv("DEFAULT_ORGANIZATION_NAME", "BidPilot Demo Org")
    org_id = stable_uuid(f"org:{org_name}")

    engine = create_engine(database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as db:
        org = db.get(Organization, org_id)
        if org is None:
            org = db.scalar(select(Organization).where(Organization.name == org_name))
        if org is None:
            stats["organizations"]["created"] += 1
            if not dry_run:
                org = Organization(
                    id=org_id,
                    name=org_name,
                    description="Imported from bidpilot demo pack",
                )
                db.add(org)
                db.flush()
        else:
            stats["organizations"]["skipped"] += 1
            org_id = org.id

        project_code = (
            project_info.get("project_code")
            or project_info.get("code")
            or project_info.get("id")
            or "DEMO-PROJECT"
        )
        project_id = stable_uuid(f"project:{project_code}")
        existing_project = db.get(BidProject, project_id)
        if existing_project is None:
            existing_project = db.scalar(
                select(BidProject).where(
                    BidProject.organization_id == org_id,
                    BidProject.project_code == str(project_code),
                )
            )

        if existing_project is None:
            stats["projects"]["created"] += 1
            if not dry_run:
                project = BidProject(
                    id=project_id,
                    organization_id=org_id,
                    project_code=str(project_code),
                    project_name=str(
                        project_info.get("project_name")
                        or project_info.get("name")
                        or "Demo Tender Project"
                    ),
                    purchaser=project_info.get("purchaser"),
                    procurement_agency=project_info.get("procurement_agency"),
                    procurement_method=project_info.get("procurement_method"),
                    industry=project_info.get("industry"),
                    region=project_info.get("region"),
                    budget_cny=_to_decimal(project_info.get("budget_cny") or project_info.get("budget")),
                    price_ceiling_cny=_to_decimal(
                        project_info.get("price_ceiling_cny") or project_info.get("price_ceiling")
                    ),
                    status=ProjectStatus.draft,
                    metadata_json={"source": "bidpilot_demo_pack", "raw": project_info},
                )
                db.add(project)
                db.flush()
        else:
            stats["projects"]["skipped"] += 1
            project_id = existing_project.id

        # Requirements — preserve original requirement_id when UUID-compatible
        req_id_map: dict[str, UUID] = {}
        for raw in requirements:
            original_id = raw.get("requirement_id") or raw.get("id") or raw.get("code")
            if original_id is None:
                continue
            original_key = str(original_id)
            try:
                req_uuid = UUID(original_key)
            except ValueError:
                req_uuid = stable_uuid(f"requirement:{original_key}")

            existing = db.get(Requirement, req_uuid)
            if existing is None:
                existing = db.scalar(
                    select(Requirement).where(
                        Requirement.project_id == project_id,
                        Requirement.requirement_code == str(
                            raw.get("requirement_code") or raw.get("code") or original_key
                        ),
                    )
                )

            if existing is not None:
                stats["requirements"]["skipped"] += 1
                req_id_map[original_key] = existing.id
                continue

            stats["requirements"]["created"] += 1
            req_id_map[original_key] = req_uuid
            if dry_run:
                continue
            req = Requirement(
                id=req_uuid,
                project_id=project_id,
                requirement_code=str(raw.get("requirement_code") or raw.get("code") or original_key),
                category=parse_category(raw.get("category") or raw.get("type")),
                title=str(raw.get("title") or raw.get("requirement") or raw.get("text") or original_key),
                normalized_requirement=raw.get("normalized_requirement") or raw.get("text"),
                mandatory=bool(raw.get("mandatory", False)),
                score=_to_decimal(raw.get("score")),
                risk_level=parse_enum(RiskLevel, raw.get("risk_level"), RiskLevel.medium),
                source_page=raw.get("source_page") or raw.get("page"),
                source_section=raw.get("source_section") or raw.get("section"),
                source_clause_id=raw.get("source_clause_id") or raw.get("clause_id"),
                evidence_required_json=raw.get("evidence_required_json") or raw.get("evidence_required"),
                quality_level=parse_enum(QualityLevel, raw.get("quality_level"), QualityLevel.silver),
                review_status=parse_enum(ReviewStatus, raw.get("review_status"), ReviewStatus.reviewed),
                metadata_json={
                    "source": "bidpilot_demo_pack",
                    "original_requirement_id": original_key,
                    "raw": raw,
                },
            )
            db.add(req)

        if not dry_run:
            db.flush()

        company_id_map: dict[str, UUID] = {}
        for raw in companies:
            name = str(raw.get("name") or raw.get("company_name") or "").strip()
            if not name:
                continue
            company_uuid = stable_uuid(f"company:{org_id}:{name}")
            existing = db.get(CompanyProfile, company_uuid)
            if existing is None and not dry_run:
                existing = db.scalar(
                    select(CompanyProfile).where(
                        CompanyProfile.organization_id == org_id,
                        CompanyProfile.name == name,
                    )
                )
            if existing is not None:
                stats["company_profiles"]["skipped"] += 1
                company_id_map[name] = existing.id
                continue
            stats["company_profiles"]["created"] += 1
            company_id_map[name] = company_uuid
            if dry_run:
                continue
            db.add(
                CompanyProfile(
                    id=company_uuid,
                    organization_id=org_id,
                    name=name,
                    credit_code=raw.get("credit_code"),
                    industry=raw.get("industry"),
                    synthetic=bool(raw.get("synthetic", True)),
                    metadata_json={"source": "bidpilot_demo_pack", "raw": raw},
                )
            )

        if not dry_run:
            db.flush()

        for raw in matches:
            req_key = str(raw.get("requirement_id") or raw.get("requirementId") or "")
            company_name = str(raw.get("company_name") or raw.get("company") or raw.get("bidder") or "")
            if not req_key or not company_name:
                continue
            req_uuid = req_id_map.get(req_key)
            if req_uuid is None:
                try:
                    req_uuid = UUID(req_key)
                except ValueError:
                    req_uuid = stable_uuid(f"requirement:{req_key}")
            company_uuid = company_id_map.get(company_name) or stable_uuid(
                f"company:{org_id}:{company_name}"
            )
            match_uuid = stable_uuid(f"match:{req_uuid}:{company_uuid}")
            existing = db.get(RequirementMatch, match_uuid)
            if existing is None and not dry_run:
                existing = db.scalar(
                    select(RequirementMatch).where(
                        RequirementMatch.requirement_id == req_uuid,
                        RequirementMatch.company_profile_id == company_uuid,
                    )
                )
            if existing is not None:
                stats["requirement_matches"]["skipped"] += 1
                continue
            stats["requirement_matches"]["created"] += 1
            if dry_run:
                continue
            # Ensure company exists for dangling references
            if db.get(CompanyProfile, company_uuid) is None:
                db.add(
                    CompanyProfile(
                        id=company_uuid,
                        organization_id=org_id,
                        name=company_name,
                        synthetic=True,
                        metadata_json={"source": "bidpilot_demo_pack", "auto_created": True},
                    )
                )
                db.flush()
            if db.get(Requirement, req_uuid) is None:
                stats["requirement_matches"]["created"] -= 1
                stats["requirement_matches"]["skipped"] += 1
                continue
            db.add(
                RequirementMatch(
                    id=match_uuid,
                    requirement_id=req_uuid,
                    company_profile_id=company_uuid,
                    status=parse_enum(MatchStatus, raw.get("status"), MatchStatus.uncertain),
                    reason=raw.get("reason") or raw.get("analysis"),
                    risk_level=parse_enum(RiskLevel, raw.get("risk_level"), None),
                    recommended_action=raw.get("recommended_action"),
                    confidence=_to_decimal(raw.get("confidence")),
                    quality_level=parse_enum(QualityLevel, raw.get("quality_level"), QualityLevel.silver),
                    review_status=parse_enum(ReviewStatus, raw.get("review_status"), ReviewStatus.reviewed),
                )
            )

        if dry_run:
            db.rollback()
        else:
            db.commit()

    return stats


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Import BidPilot demo pack")
    parser.add_argument("--demo-path", default=None, help="Path to bidpilot_demo_pack or demo_data")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    demo_root = find_demo_root(args.demo_path)
    if demo_root is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "bidpilot_demo_pack / demo_data with project_info.json not found",
                    "hint": "Place pack under ./demo_data or pass --demo-path",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    database_url = args.database_url or os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://bidpilot:change_me_postgres@localhost:5432/bidpilot",
    )

    # Dry-run without DB: if cannot connect, still report file-level stats.
    if args.dry_run:
        try:
            stats = import_demo(demo_root=demo_root, dry_run=True, database_url=database_url)
            stats["ok"] = True
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:  # noqa: BLE001
            # Offline dry-run from files only
            reqs = as_list(load_json(demo_root / "requirements.json")) if (demo_root / "requirements.json").exists() else []
            matches = (
                as_list(load_json(demo_root / "qualification_matches.json"))
                if (demo_root / "qualification_matches.json").exists()
                else []
            )
            project = load_json(demo_root / "project_info.json") if (demo_root / "project_info.json").exists() else {}
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "database_reachable": False,
                        "database_error": str(exc),
                        "demo_root": str(demo_root),
                        "file_counts": {
                            "project_info": 1 if project else 0,
                            "requirements": len(reqs),
                            "qualification_matches": len(matches),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

    stats = import_demo(demo_root=demo_root, dry_run=False, database_url=database_url)
    stats["ok"] = True
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
