"""Text extraction for uploaded tender documents.

Supported: PDF (pypdf), DOCX (python-docx), TXT, HTML/HTM (BeautifulSoup),
XLSX (openpyxl). Anything else, plus scanned PDFs without an extractable text
layer, is reported honestly as failed / ocr_required. No fake results.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from app.models.enums import ParseStatus

PARSER_NAME = "bidpilot-basic-parser"
PARSER_VERSION = "1.0.0"

# A PDF whose pages yield fewer characters than this on average is treated as
# a scanned document that needs OCR.
_MIN_CHARS_PER_PAGE = 10


@dataclass
class ParseResult:
    status: ParseStatus
    text: str = ""
    page_count: int | None = None
    error: str | None = None

    @property
    def extracted_characters(self) -> int:
        return len(self.text)


def parse_document(content: bytes, extension: str) -> ParseResult:
    ext = extension.lower().lstrip(".")
    try:
        if ext == "pdf":
            return _parse_pdf(content)
        if ext == "docx":
            return _parse_docx(content)
        if ext == "txt":
            return _parse_txt(content)
        if ext in {"html", "htm"}:
            return _parse_html(content)
        if ext == "xlsx":
            return _parse_xlsx(content)
        return ParseResult(
            status=ParseStatus.failed,
            error=f"不支持解析的文件类型: .{ext}（DOC/WPS 等格式请先转换为 PDF 或 DOCX）",
        )
    except Exception as exc:  # noqa: BLE001 - parse failures must not crash the task
        return ParseResult(status=ParseStatus.failed, error=f"解析异常: {exc}")


def _parse_pdf(content: bytes) -> ParseResult:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            return ParseResult(status=ParseStatus.failed, error="PDF 已加密，无法提取文本")

    page_count = len(reader.pages)
    pages_text: list[str] = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - keep going; a single bad page is not fatal
            pages_text.append("")

    text = "\n".join(part.strip() for part in pages_text if part.strip())
    if page_count > 0 and len(text) < _MIN_CHARS_PER_PAGE * page_count:
        return ParseResult(
            status=ParseStatus.ocr_required,
            text=text,
            page_count=page_count,
            error="PDF 无可提取文本层，疑似扫描件，需要 OCR",
        )
    return ParseResult(status=ParseStatus.success, text=text, page_count=page_count)


def _parse_docx(content: bytes) -> ParseResult:
    from docx import Document as DocxDocument

    document = DocxDocument(io.BytesIO(content))
    parts: list[str] = [para.text for para in document.paragraphs if para.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append("\t".join(cells))
    text = "\n".join(parts)
    if not text.strip():
        return ParseResult(status=ParseStatus.failed, error="DOCX 中未提取到任何文本")
    return ParseResult(status=ParseStatus.success, text=text)


def _parse_txt(content: bytes) -> ParseResult:
    text: str | None = None
    for encoding in ("utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        return ParseResult(status=ParseStatus.failed, error="无法识别文本编码")
    if not text.strip():
        return ParseResult(status=ParseStatus.failed, error="文本文件内容为空")
    return ParseResult(status=ParseStatus.success, text=text)


def _parse_html(content: bytes) -> ParseResult:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "head"]):
        tag.decompose()
    text = "\n".join(
        line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()
    )
    if not text:
        return ParseResult(status=ParseStatus.failed, error="HTML 中未提取到可读正文")
    return ParseResult(status=ParseStatus.success, text=text)


def _parse_xlsx(content: bytes) -> ParseResult:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for sheet in workbook.worksheets:
            parts.append(f"[工作表] {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = [str(value).strip() for value in row if value is not None]
                if cells:
                    parts.append("\t".join(cells))
    finally:
        workbook.close()
    text = "\n".join(parts)
    has_cell_content = any(not part.startswith("[工作表] ") for part in parts)
    if not has_cell_content:
        return ParseResult(status=ParseStatus.failed, error="XLSX 中未提取到任何单元格文本")
    return ParseResult(status=ParseStatus.success, text=text)
