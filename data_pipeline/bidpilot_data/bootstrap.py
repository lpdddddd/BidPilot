from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.settings import Settings, get_settings, override_settings
from bidpilot_data.utils import ensure_dir, sha256_bytes, stable_uuid, write_jsonl

log = get_logger(__name__)


DEMO_TENDER_TEXT = """智慧园区安防系统采购项目招标文件（演示文本，非真实公告）

第一章 项目概况
项目编号：DEMO-2026-001
项目名称：智慧园区安防系统采购项目
采购人：某市城投集团
预算金额：3500000元
最高限价：3200000元
投标截止时间：2026年8月1日17:00

第二章 投标人资格要求
2.1 投标人须提供有效营业执照。
2.2 投标人应当具备信息系统集成相关资质。
2.3 投标人不得处于被责令停业或破产状态，否则投标无效。

第三章 技术要求
3.1 系统应支持不少于200路视频接入。
3.2 平台必须支持国密算法加密传输。

第四章 商务要求
4.1 履约保证金按合同金额的5%提交。
4.2 付款方式分三期支付。

第五章 评分办法
5.1 本项目采用综合评分法，技术分60分，商务分30分，价格分10分。
5.2 类似项目业绩每个得3分，最高15分。

第六章 否决性条款
6.1 未按要求密封投标文件的，按废标处理。
6.2 报价超过最高限价的，应当否决投标。
"""


@contextmanager
def use_demo_fixture_root() -> Iterator[Path]:
    """Redirect datasets_root to datasets/fixtures/demo for isolated fixture runs."""
    base = get_settings()
    fixture_root = ensure_dir(base.repo_root / "datasets" / "fixtures" / "demo")

    class FixtureSettings(Settings):
        @property
        def datasets_root(self) -> Path:  # type: ignore[override]
            return fixture_root

    prev = Settings(
        database_url=base.database_url,
        openai_api_key=base.openai_api_key,
        openai_base_url=base.openai_base_url,
        dataset_model_name=base.dataset_model_name,
        model_name=base.model_name,
        repo_root=base.repo_root,
        pipeline_config_path=base.pipeline_config_path,
    )
    override_settings(FixtureSettings(repo_root=base.repo_root, database_url=base.database_url))
    try:
        yield fixture_root
    finally:
        override_settings(prev)


def bootstrap_from_demo(*, dry_run: bool = False) -> dict[str, Any]:
    """Create local file:// fixture under datasets/fixtures/demo only.

    This must never be mixed into formal training manifests under datasets/manifests
    for production builds.
    """
    settings = get_settings()
    # Force fixture root even if caller forgot the context manager.
    fixture_marker = settings.datasets_root
    if "fixtures" not in str(fixture_marker):
        ensure_dir(settings.repo_root / "datasets" / "fixtures" / "demo")
        # Soft-redirect for this call.
        from bidpilot_data.settings import Settings as S

        class _Tmp(S):
            @property
            def datasets_root(self) -> Path:  # type: ignore[override]
                return settings.repo_root / "datasets" / "fixtures" / "demo"

        override_settings(_Tmp(repo_root=settings.repo_root, database_url=settings.database_url))
        settings = get_settings()

    demo = settings.repo_root / "demo_data"
    project_info = {}
    if (demo / "project_info.json").exists():
        project_info = json.loads((demo / "project_info.json").read_text(encoding="utf-8"))

    project_code = str(project_info.get("project_code") or "DEMO-2026-001")
    project_name = str(project_info.get("project_name") or "Demo Tender Project")
    project_id = str(stable_uuid(f"project:{project_code}"))

    raw_dir = ensure_dir(settings.datasets_root / "raw" / "documents" / project_code)
    text_path = raw_dir / "tender_demo.txt"
    content = DEMO_TENDER_TEXT.encode("utf-8")
    digest = sha256_bytes(content)
    document_id = str(stable_uuid(f"document:{digest}"))
    source_id = str(stable_uuid(f"source:{project_code}:tender_demo"))

    if not dry_run:
        text_path.write_bytes(content)

    file_url = text_path.resolve().as_uri()
    source = {
        "source_id": source_id,
        "source_url": file_url,
        "source_site": "local_demo_fixture",
        "project_code": project_code,
        "project_name": project_name,
        "document_type": "tender",
        "published_at": None,
        "province": project_info.get("region"),
        "industry": project_info.get("industry"),
        "license_or_terms": "local demo fixture for unit tests only; not a real procurement notice",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "fixture": True,
    }
    manifest_path = ensure_dir(settings.datasets_root / "manifests") / "source_manifest.jsonl"
    if not dry_run:
        write_jsonl(manifest_path, [source])
        from bidpilot_data.schemas import DocumentRecord, DocumentType, ParseStatus

        doc = DocumentRecord(
            document_id=document_id,
            project_id=project_id,
            source_id=source_id,
            original_filename="tender_demo.txt",
            mime_type="text/plain",
            sha256=digest,
            file_size=len(content),
            storage_path=str(text_path.relative_to(settings.datasets_root)),
            page_count=1,
            parse_method=None,
            parse_status=ParseStatus.pending,
            document_type=DocumentType.tender,
            source_url=file_url,
        )
        write_jsonl(settings.datasets_root / "manifests" / "documents.jsonl", [doc])
        write_jsonl(settings.datasets_root / "manifests" / "sources.jsonl", [source])

    stats = {
        "project_code": project_code,
        "project_id": project_id,
        "document_id": document_id,
        "manifest": str(manifest_path),
        "datasets_root": str(settings.datasets_root),
        "fixture_only": True,
        "dry_run": dry_run,
    }
    log_stats(log, "bootstrap_demo_fixture", stats)
    return stats
