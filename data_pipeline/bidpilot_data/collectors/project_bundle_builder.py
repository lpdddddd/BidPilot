from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from bidpilot_data.schemas import BundleLevel, DocumentType, ProjectBundle, ProjectDocumentRef
from bidpilot_data.utils import stable_uuid


TENDER_FILE_TYPES = {
    DocumentType.tender,
    DocumentType.tender_document,
}
TENDER_NOTICE_TYPES = {
    DocumentType.tender_notice,
    DocumentType.announcement,
}
AWARD_TYPES = {
    DocumentType.award_notice,
    DocumentType.result,
}
CONTRACT_OR_EVAL_TYPES = {
    DocumentType.contract_notice,
    DocumentType.contract,
    DocumentType.evaluation_result,
}


def make_project_id(project_code: str, purchaser: str | None, project_name: str) -> str:
    key = f"project:{project_code}|{purchaser or ''}|{project_name}"
    return str(stable_uuid(key))


def compute_bundle_level(doc_types: set[DocumentType]) -> BundleLevel:
    has_tender_file = bool(doc_types & TENDER_FILE_TYPES) or DocumentType.tender in doc_types
    # HTML tender notice alone is not a full procurement file; require tender_document/tender PDF.
    has_award = bool(doc_types & AWARD_TYPES)
    has_contract_or_eval = bool(doc_types & CONTRACT_OR_EVAL_TYPES)
    if has_tender_file and has_award and has_contract_or_eval:
        return BundleLevel.level_a
    if has_tender_file and has_award:
        return BundleLevel.level_b
    if has_tender_file:
        return BundleLevel.level_c
    return BundleLevel.incomplete


def _normalize_code(code: str | None) -> str | None:
    if not code:
        return None
    code = code.strip()
    code = code.replace("（", "(").replace("）", ")")
    return code or None


def bundle_key(meta: dict[str, Any]) -> str:
    code = _normalize_code(meta.get("project_code"))
    if code and code != "UNKNOWN":
        return f"code:{code}"
    name = (meta.get("project_name") or meta.get("title") or "").strip()
    purchaser = (meta.get("purchaser") or "").strip()
    published = (meta.get("published_at") or "")[:10]
    # Avoid collapsing distinct UNKNOWN projects that only share purchaser/date.
    notice = str(meta.get("official_project_url") or meta.get("source_url") or "")
    return f"name:{name}|{purchaser}|{published}|{notice}"


def build_project_bundles(items: list[dict[str, Any]]) -> list[ProjectBundle]:
    """Merge notices/attachments that belong to the same project."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(bundle_key(item), []).append(item)

    bundles: list[ProjectBundle] = []
    for _key, rows in groups.items():
        rows_sorted = sorted(rows, key=lambda r: str(r.get("published_at") or ""))
        primary = next(
            (
                r
                for r in rows_sorted
                if str(r.get("document_type")) in {"tender_document", "tender", "tender_notice"}
            ),
            rows_sorted[0],
        )
        project_code = _normalize_code(primary.get("project_code")) or "UNKNOWN"
        project_name = str(primary.get("project_name") or primary.get("title") or "UNKNOWN")
        purchaser = primary.get("purchaser")
        project_id = make_project_id(project_code, purchaser, project_name)

        docs: list[ProjectDocumentRef] = []
        seen_urls: set[str] = set()
        doc_types: set[DocumentType] = set()
        for r in rows_sorted:
            url = str(r.get("source_url") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                dtype = DocumentType(str(r.get("document_type") or "other"))
            except ValueError:
                dtype = DocumentType.other
            doc_types.add(dtype)
            docs.append(
                ProjectDocumentRef(
                    document_type=dtype,
                    source_url=url,
                    sha256=r.get("sha256"),
                    published_at=r.get("published_at"),
                    local_path=r.get("local_path"),
                    original_filename=r.get("original_filename"),
                    document_id=r.get("document_id"),
                )
            )

        official_url = str(primary.get("official_project_url") or primary.get("source_url"))
        domain = urlparse(official_url).netloc.lower().split(":")[0]
        bundles.append(
            ProjectBundle(
                project_id=project_id,
                project_code=project_code,
                project_name=project_name,
                province=primary.get("province"),
                industry=primary.get("industry"),
                purchaser=purchaser,
                procurement_agency=primary.get("procurement_agency"),
                budget_cny=primary.get("budget_cny"),
                published_at=primary.get("published_at"),
                official_project_url=official_url,
                bundle_level=compute_bundle_level(doc_types),
                documents=docs,
                source_domain=domain,
                issuing_authority=purchaser,
                collected_at=datetime.now(timezone.utc).isoformat(),
            )
        )
    return bundles


def filename_suggests_tender_document(name: str) -> bool:
    """True only when the attachment name clearly indicates a procurement file package."""
    if not name:
        return False
    if any(k in name for k in ("中标通知", "成交通知", "结果公告", "合同文本", "中标结果", "声明函", "登记表")):
        return False
    return any(
        k in name
        for k in (
            "招标文件",
            "采购文件",
            "磋商文件",
            "谈判文件",
            "询价文件",
            "竞争文件",
            "竞价文件",
            "招标公文",
            "采购需求文件",
            "发售稿",
            "招标公告.docx",
            "招标公告.pdf",
            "采购公告",
            "磋商公告",
        )
    )


def attachment_type_for_notice(notice_type: DocumentType, filename: str) -> DocumentType:
    """Classify attachment using notice context + filename (no fake tender_document labels)."""
    if filename_suggests_tender_document(filename):
        return DocumentType.tender_document
    if notice_type in {DocumentType.contract_notice, DocumentType.contract} and "合同" in (filename or ""):
        return DocumentType.contract_notice
    if notice_type in {
        DocumentType.tender_notice,
        DocumentType.announcement,
        DocumentType.other_notice,
    }:
        lower = (filename or "").lower()
        if lower.endswith((".pdf", ".doc", ".docx", ".zip", ".rar")) and not any(
            k in filename for k in ("中标", "成交", "合同", "结果")
        ):
            # Public attachment under a tender/procurement notice is treated as tender package.
            return DocumentType.tender_document
    return DocumentType.other
