from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from rapidfuzz import fuzz

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import (
    ChunkRecord,
    Difficulty,
    QualityLevel,
    QuestionType,
    RAGQuestion,
    ReviewStatus,
)
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, stable_uuid, write_json, write_jsonl

log = get_logger(__name__)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def split_multi_section_answer(answer: str) -> tuple[str, str] | None:
    """Split combined multi_section answer into two evidence-backed parts."""
    a = (answer or "").strip()
    if not a:
        return None
    for sep in ("；另方面，", ";另方面，", "；另一方面，", "。另方面，", "；另外，"):
        if sep in a:
            p1, p2 = a.split(sep, 1)
            if p1.strip() and p2.strip():
                return p1.strip(), p2.strip()
    # Fallback: middle split if two sentences
    parts = re.split(r"[；;。]", a)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def multi_section_dual_answer_ok(answer: str, quote1: str, quote2: str) -> bool:
    """Each answer part must be supported by the corresponding quote (not only concat overlap)."""
    parts = split_multi_section_answer(answer)
    if not parts:
        return False
    a1, a2 = parts
    q1, q2 = (quote1 or "").strip(), (quote2 or "").strip()
    if not q1 or not q2:
        return False

    def supported(part: str, quote: str) -> bool:
        if not part or not quote:
            return False
        if _norm(part[:24]) in _norm(quote) or _norm(quote[:24]) in _norm(part):
            return True
        return fuzz.partial_ratio(part[:120], quote[:400]) >= 55

    return supported(a1, q1) and supported(a2, q2)

# Scenario templates close to real procurement IT/ops projects (must pass corpus absence check).
UNANSWERABLE_TEMPLATES = [
    "采购文件是否规定质保期内每月巡检次数？",
    "中标供应商项目经理是否必须具有高级职称？",
    "合同是否约定提前交付奖励？",
    "采购人是否提供原系统源代码？",
    "项目是否要求驻场人员夜间值班？",
    "是否允许联合体中的境外企业参与？",
    "是否规定数据迁移后的历史数据保存年限？",
    "是否要求供应商提供指定品牌服务器？",
    "是否约定系统故障的具体赔偿金额？",
    "是否公开了所有评审专家的评分明细？",
    "是否要求投标人提供等保测评报告原件？",
    "是否规定实施期间每周必须提交进度周报？",
    "是否要求提供国产化信创适配认证？",
    "是否约定免费培训不少于多少人次？",
    "是否要求售后响应时间不超过两小时？",
    "是否规定必须使用采购人指定的云平台账号？",
    "是否要求投标保证金以电子保函以外形式提交？",
    "是否约定项目验收后三年免费升级？",
    "是否要求项目团队核心成员不少于五名本地户籍人员？",
    "是否公开了详细的历史成交价格区间？",
    "是否要求提供ISO27001证书且在有效期内？",
    "是否约定接口联调必须在采购人机房完成？",
    "是否要求投标文件加密提交并上传至指定平台？",
    "是否规定备份数据必须异地存放？",
    "是否要求中标人开具增值税专用发票后才付款？",
    "是否约定延期罚款按日计算的具体比例？",
    "是否要求提供源代码第三方安全审计报告？",
    "是否规定驻场工程师必须持有PMP证书？",
    "是否要求运维期间提供7×24小时热线？",
    "是否约定知识产权全部归采购人独家所有？",
    "是否要求投标人在本地设立固定经营场所？",
    "是否公开评标委员会成员名单？",
    "是否要求提供近三年连续盈利的审计报告？",
    "是否约定试运行不少于九十个自然日？",
    "是否要求系统支持不少于一万并发用户？",
    "是否规定数据销毁必须经采购人书面确认？",
    "是否要求投标人具备涉密信息系统集成资质？",
    "是否约定故障恢复时间目标（RTO）的具体数值？",
    "是否要求提供英文版操作手册？",
    "是否规定必须采用微服务架构交付？",
    "是否要求中标后十五日内提交实施方案？",
    "是否约定履约保证金比例及退还条件？",
    "是否要求对历史数据做逐条人工核对？",
    "是否公开采购预算对应的明细科目？",
    "是否要求供应商承诺不使用开源代码？",
    "是否约定采购人可无理由终止合同？",
    "是否要求提供省级以上科技进步奖证明？",
    "是否规定培训必须在省外指定基地进行？",
    "是否要求投标人通过特定银行开具保函？",
    "是否约定软件缺陷终身免费修复？",
    "是否要求所有服务器部署在采购人指定机柜位？",
    "是否规定联合体成员均须参加澄清答疑会？",
    "是否要求提供近五年同类项目不少于十个？",
    "是否约定验收不通过时全额退还已付款？",
]

LEAK_MARKERS = ("原文：", "根据原文", "根据下列原文", "以下条款", "请依据采购文件原文", "只依据原文")

TYPE_RATIOS = {
    QuestionType.project_basic: 0.10,
    QuestionType.qualification: 0.20,
    QuestionType.scoring: 0.15,
    QuestionType.commercial: 0.10,
    QuestionType.technical: 0.15,
    QuestionType.rejection: 0.10,
    QuestionType.time_location: 0.05,
    QuestionType.evidence: 0.05,
    QuestionType.multi_section: 0.05,
}


def _longest_common_substr_len(a: str, b: str) -> int:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    best = 0
    for i in range(len(a)):
        for j in range(i + 20, len(a) + 1):
            if a[i:j] in b:
                best = max(best, j - i)
            else:
                break
    return best


def question_leaks_quote(question: str, quote: str) -> bool:
    q = question or ""
    if any(m in q for m in LEAK_MARKERS):
        return True
    if quote and _longest_common_substr_len(q, quote) >= 20:
        return True
    return False


def fuzz_partial(a: str, b: str) -> int:
    from rapidfuzz import fuzz

    return int(fuzz.partial_ratio((a or "")[:180], (b or "")[:500]))


def _sentence_spans(text: str) -> list[str]:
    parts = re.split(r"(?<=[。；\n])", text)
    return [p.strip() for p in parts if len(p.strip()) >= 20]


def _pick_requirement_like(chunk: ChunkRecord) -> str | None:
    for sent in _sentence_spans(chunk.text):
        if any(k in sent for k in ("应当", "必须", "不得", "投标人", "供应商", "评分", "资质", "预算", "截止", "废标", "无效")):
            return sent[:220]
    spans = _sentence_spans(chunk.text)
    return spans[0][:220] if spans else None


def _short_entity(text: str, *, max_len: int = 18) -> str:
    """Extract a short entity/title fragment safe to put in a question (not a long quote)."""
    t = re.sub(r"\s+", "", text or "")
    t = re.sub(r"[。；;：:，,、].*$", "", t)
    return t[:max_len]


def _natural_question(qtype: QuestionType, req: dict[str, Any] | None, quote: str, project: dict[str, Any]) -> str | None:
    """Build a natural user question that does not paste the source quote."""
    title = _short_entity((req or {}).get("title") or "")
    cat = (req or {}).get("category") or ""
    pname = _short_entity(project.get("project_name") or "", max_len=20)
    pcode = (project.get("project_code") or "")[:24]
    # Distinctive noun phrases from quote (short) for diversity without leakage
    nouns = [
        m.group(0)
        for m in re.finditer(
            r"(营业执照|资质证书|社保|财务报表|业绩|服务期限|付款|履约保证金|无效投标|废标|技术分|商务分|响应文件|联合体|扣分|加分)",
            quote or "",
        )
    ]
    noun = nouns[0] if nouns else ""
    scoring_hint = ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*分", quote or "")
    if m:
        scoring_hint = m.group(1)

    templates: dict[QuestionType, list[str]] = {
        QuestionType.project_basic: [
            f"项目「{pname}」的采购人与项目编号分别是什么？" if pname else "本项目的采购人是谁？",
            f"编号 {pcode} 的项目预算与采购方式如何约定？" if pcode else "本项目预算与采购方式在文件中如何约定？",
            f"「{pname}」的基本采购信息有哪些？" if pname else "本项目的基本采购信息有哪些？",
        ],
        QuestionType.qualification: [
            f"项目「{pname}」对投标人的{noun or '资格'}有什么要求？" if pname else f"本项目对投标人的{noun or '资格条件'}有什么要求？",
            f"投标人就「{title}」需要提交哪些证明材料？" if title else "投标人需要提交哪些资格证明材料？",
            f"关于「{title}」的资格要求是什么？" if title else "投标人财务或业绩方面有什么资格要求？",
            "本项目对投标人的财务状况有什么要求？",
        ],
        QuestionType.scoring: [
            f"项目「{pname}」采用什么评分方法？" if pname else "项目采用什么评分方法？",
            f"技术部分最高可以获得多少分？" if scoring_hint else "评分办法中技术部分如何计分？",
            f"评分项「{title or noun or '技术'}」如何赋分？",
            f"综合评分法中与「{noun or title or '商务'}」相关的分值如何规定？",
        ],
        QuestionType.commercial: [
            f"「{pname}」商务条款中对{noun or '付款或履约'}有哪些约定？" if pname else f"商务条款中对{noun or '付款或履约'}有哪些约定？",
            f"投标报价或「{title}」商务响应需要满足什么要求？" if title else "投标报价或商务响应需要满足什么要求？",
        ],
        QuestionType.technical: [
            f"技术部分关于「{title or noun or '功能'}」需要满足哪些要求？",
            f"「{pname}」技术条款有哪些硬性要求？" if pname else "系统功能或性能方面有哪些硬性要求？",
        ],
        QuestionType.rejection: [
            f"项目「{pname}」中哪些情况会导致投标无效？" if pname else "哪些情况会导致投标无效？",
            f"与「{title or noun or '响应文件'}」相关的否决/废标情形是什么？",
            "未按要求密封或签署会怎样处理？",
        ],
        QuestionType.time_location: [
            f"「{pname}」的服务期限或履行期限是多久？" if pname else "本项目的服务期限或履行期限是多久？",
            f"编号 {pcode} 的投标截止时间与开标地点如何约定？" if pcode else "投标截止时间与开标地点如何约定？",
            "项目交付或服务地点在哪里？",
        ],
        QuestionType.evidence: [
            f"响应「{title or noun or '资格'}」通常需要准备哪些证明材料？",
            f"「{pname}」业绩或认证材料应如何提交？" if pname else "业绩或认证材料应如何提交？",
        ],
        QuestionType.multi_section: [
            f"请结合「{pname}」资格与否决条款说明投标人需同时注意什么？" if pname else "请结合资格与否决条款说明投标人需同时满足哪些关键义务？",
            "评分办法与资格要求之间有哪些需要同时关注的条件？",
        ],
    }
    # Prefer category-aligned templates
    if cat == "mandatory_rejection":
        qtype = QuestionType.rejection
    elif cat == "scoring":
        qtype = QuestionType.scoring
    elif cat in {"qualification", "performance", "certification", "personnel"}:
        qtype = QuestionType.qualification
    elif cat == "project_info":
        qtype = QuestionType.project_basic

    opts = templates.get(qtype) or ["本项目相关条款有哪些关键要求？"]
    # Rotate by requirement id for diversity
    rid = str((req or {}).get("requirement_id") or "")
    rot = sum(ord(c) for c in rid) % max(len(opts), 1)
    ordered = opts[rot:] + opts[:rot]
    for cand in ordered:
        if cand and not question_leaks_quote(cand, quote):
            return cand
    fallback = f"本项目文件对{noun or '投标人'}有哪些必须满足的要求？"
    if not question_leaks_quote(fallback, quote):
        return fallback
    return None


def _answer_from_quote(quote: str, chunk_text: str) -> str | None:
    """Grounded answer supported by quote; never use bare chunk first line.

    Answer must be a contiguous substring of the quote (no ellipsis truncation)
    so validators can verify evidence support.
    """
    q = re.sub(r"\s+", " ", quote).strip()
    if len(q) < 12:
        return None
    if _norm(q) not in _norm(chunk_text) and _norm(q[:40]) not in _norm(chunk_text):
        # Fall back to a sentence inside the chunk that overlaps quote tokens
        for sent in _sentence_spans(chunk_text):
            if len(sent) >= 20 and _longest_common_substr_len(sent, q) >= 12:
                q = re.sub(r"\s+", " ", sent).strip()[:220]
                break
        else:
            return None
    first_line = chunk_text.strip().splitlines()[0].strip() if chunk_text.strip() else ""
    if q == first_line and len(q) < 40:
        spans = _sentence_spans(chunk_text)
        for s in spans[1:]:
            if len(s) >= 20 and _norm(s[:40]) in _norm(chunk_text):
                q = re.sub(r"\s+", " ", s).strip()[:220]
                break
        else:
            return None
    # Keep as contiguous text without ellipsis so quote support checks pass
    return q[:180] if len(q) > 180 else q


def _corpus_has_any(corpus: str, keywords: list[str]) -> bool:
    return any(k and k in corpus for k in keywords)


def _unanswerable_keywords(question: str) -> list[str]:
    """Distinctive phrases used to verify absence in project corpus.

    Prefer multi-character distinctive spans; single common IT nouns are ignored alone.
    """
    distinctive = [
        "每月巡检",
        "高级职称",
        "提前交付奖励",
        "原系统源代码",
        "夜间值班",
        "境外企业",
        "历史数据保存年限",
        "指定品牌服务器",
        "具体赔偿金额",
        "评审专家的评分明细",
        "等保测评报告原件",
        "进度周报",
        "信创适配认证",
        "免费培训不少于",
        "两小时",
        "指定的云平台账号",
        "电子保函以外",
        "三年免费升级",
        "本地户籍人员",
        "历史成交价格区间",
        "ISO27001",
        "采购人机房完成",
        "加密提交",
        "异地存放",
        "增值税专用发票后才付款",
        "延期罚款按日",
        "第三方安全审计",
        "PMP证书",
        "7×24",
        "7x24",
        "独家所有",
        "固定经营场所",
        "评标委员会成员名单",
        "连续盈利的审计报告",
        "九十个自然日",
        "一万并发",
        "数据销毁",
        "涉密信息系统集成",
        "RTO",
        "英文版操作手册",
        "微服务架构",
        "十五日内提交实施方案",
        "履约保证金比例",
        "逐条人工核对",
        "预算对应的明细科目",
        "不使用开源代码",
        "无理由终止合同",
        "科技进步奖",
        "省外指定基地",
        "特定银行开具保函",
        "终身免费修复",
        "指定机柜位",
        "澄清答疑会",
        "同类项目不少于十个",
        "全额退还已付款",
    ]
    found = [d for d in distinctive if d in (question or "")]
    if found:
        return found
    toks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{4,16}", question or "")
    stop = {
        "是否规定",
        "是否要求",
        "是否约定",
        "是否公开",
        "是否允许",
        "采购文件",
        "中标供应商",
        "项目经理",
        "投标人",
        "供应商",
    }
    return [t for t in toks if t not in stop][:3]


def _infer_type_from_req(req: dict[str, Any]) -> QuestionType:
    cat = req.get("category") or ""
    mapping = {
        "qualification": QuestionType.qualification,
        "performance": QuestionType.qualification,
        "certification": QuestionType.qualification,
        "personnel": QuestionType.qualification,
        "scoring": QuestionType.scoring,
        "mandatory_rejection": QuestionType.rejection,
        "project_info": QuestionType.project_basic,
        "technical": QuestionType.technical,
        "commercial": QuestionType.commercial,
        "timeline": QuestionType.time_location,
    }
    return mapping.get(cat, QuestionType.qualification)


def build_rag_eval(*, dry_run: bool = False, limit: int | None = 300) -> dict[str, Any]:
    settings = get_settings()
    projects = {
        p["project_id"]: p
        for p in read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
        if p.get("bundle_level") in {"level_a", "level_b", "level_c"}
        and p.get("project_code") != "PORTAL_SNAPSHOT"
        and not str(p.get("project_name") or "").startswith("official_portal_snapshot")
    }
    chunks_all = [
        ChunkRecord.model_validate(r)
        for r in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")
        if r.get("project_id") in projects
    ]
    docs = {
        d["document_id"]: d
        for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
        if d.get("project_code") != "PORTAL_SNAPSHOT"
    }
    reqs = [
        r
        for r in (
            read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl")
            + read_jsonl(settings.datasets_root / "gold" / "requirements.jsonl")
        )
        if r.get("project_id") in projects
    ]

    statements_by_project: dict[str, str] = defaultdict(str)
    chunks_by_id = {c.chunk_id: c for c in chunks_all}
    for c in chunks_all:
        statements_by_project[c.project_id] += "\n" + c.text

    # Prefer level_a/b, level_c only for single-doc
    ab_pids = {pid for pid, p in projects.items() if p.get("bundle_level") in {"level_a", "level_b"}}
    target = limit if limit is not None else 300
    answerable_target = max(1, int(target * 0.88))  # leave room for 10-15% unanswerable after dedup

    type_caps = {qt: max(1, int(answerable_target * ratio)) for qt, ratio in TYPE_RATIOS.items()}
    type_counts: Counter[QuestionType] = Counter()
    per_project: Counter[str] = Counter()
    per_chunk: Counter[str] = Counter()
    max_per_project = max(3, int(target * 0.10))

    questions: list[RAGQuestion] = []

    # 1) Requirement-grounded natural QA (priority)
    priority_cats = {"qualification", "scoring", "mandatory_rejection", "project_info", "technical", "commercial", "timeline"}
    ranked_reqs = sorted(
        reqs,
        key=lambda r: (
            0 if r.get("category") in priority_cats else 1,
            0 if r.get("mandatory") else 1,
            0 if r.get("risk_level") in {"critical", "high"} else 1,
            -(float(r.get("confidence") or 0)),
        ),
    )

    for req in ranked_reqs:
        if sum(1 for q in questions if q.answerable) >= answerable_target:
            break
        pid = req["project_id"]
        proj = projects.get(pid) or {}
        level = proj.get("bundle_level")
        if level not in {"level_a", "level_b", "level_c"}:
            continue
        if per_project[pid] >= max_per_project:
            continue
        chunk_id = req.get("chunk_id")
        quote = (req.get("original_text") or "").strip()
        if not chunk_id or chunk_id not in chunks_by_id or len(quote) < 16:
            continue
        chunk = chunks_by_id[chunk_id]
        if per_chunk[chunk_id] >= 2:
            continue
        qtype = _infer_type_from_req(req)
        if type_counts[qtype] >= type_caps.get(qtype, answerable_target):
            continue
        if level == "level_c" and qtype == QuestionType.multi_section:
            continue
        question = _natural_question(qtype, req, quote, proj)
        if not question:
            continue
        answer = _answer_from_quote(quote, chunk.text)
        if not answer:
            continue
        if answer not in chunk.text and _norm(answer.rstrip("…")) not in _norm(chunk.text):
            # allow truncated answer if prefix in chunk
            if _norm(answer.rstrip("…")[:40]) not in _norm(chunk.text):
                continue
        src_url = req.get("source_url") or (docs.get(chunk.document_id) or {}).get("source_url")
        if not src_url:
            continue
        page = req.get("source_page") or chunk.page_start
        questions.append(
            RAGQuestion(
                question_id=str(stable_uuid(f"ragq:req:{req['requirement_id']}:{qtype.value}")),
                project_id=pid,
                question=question,
                answer=answer,
                answerable=True,
                gold_chunk_ids=[chunk_id],
                gold_document_ids=[req["document_id"]] if req.get("document_id") else [chunk.document_id],
                source_document_ids=[req["document_id"]] if req.get("document_id") else [chunk.document_id],
                source_urls=[src_url],
                source_pages=[int(page)],
                source_quotes=[quote[:220]],
                question_type=qtype,
                difficulty=Difficulty.medium,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )
        type_counts[qtype] += 1
        per_project[pid] += 1
        per_chunk[chunk_id] += 1

    # 2) Multi-section from level_a/b with complementary chunks (not first two)
    COMPLEMENTARY = [
        (("资格", "资质", "投标人"), ("否决", "无效", "废标"), "资格要求与否决情形"),
        (("技术", "功能", "性能"), ("评分", "分值", "综合评分"), "技术要求与评分规则"),
        (("服务期限", "履约期限", "合同期限"), ("付款", "结算", "履约保证金"), "服务期限与商务付款"),
        (("人员", "项目经理", "团队"), ("证明材料", "证书", "社保证明"), "人员要求与证明材料"),
    ]

    def _chunk_hits(chunk: ChunkRecord, keys: tuple[str, ...]) -> int:
        t = chunk.text or ""
        return sum(1 for k in keys if k in t)

    for pid in sorted(ab_pids):
        if sum(1 for q in questions if q.answerable) >= answerable_target:
            break
        if type_counts[QuestionType.multi_section] >= type_caps[QuestionType.multi_section]:
            break
        if per_project[pid] >= max_per_project:
            continue
        p_chunks = [c for c in chunks_all if c.project_id == pid]
        if len(p_chunks) < 2:
            continue
        pair = None
        theme = ""
        for keys_a, keys_b, theme_name in COMPLEMENTARY:
            cands_a = sorted(p_chunks, key=lambda c: -_chunk_hits(c, keys_a))
            cands_b = sorted(p_chunks, key=lambda c: -_chunk_hits(c, keys_b))
            if not cands_a or not cands_b:
                continue
            if _chunk_hits(cands_a[0], keys_a) < 1 or _chunk_hits(cands_b[0], keys_b) < 1:
                continue
            for ca in cands_a[:5]:
                for cb in cands_b[:5]:
                    if ca.chunk_id == cb.chunk_id:
                        continue
                    path_a = (ca.section_path or "") or f"doc:{ca.document_id}:p{ca.page_start}"
                    path_b = (cb.section_path or "") or f"doc:{cb.document_id}:p{cb.page_start}"
                    if path_a == path_b and ca.document_id == cb.document_id and ca.page_start == cb.page_start:
                        continue
                    if per_chunk[ca.chunk_id] >= 2 or per_chunk[cb.chunk_id] >= 2:
                        continue
                    pair = (ca, cb)
                    theme = theme_name
                    break
                if pair:
                    break
            if pair:
                break
        if not pair:
            continue
        c1, c2 = pair
        q1 = _pick_requirement_like(c1)
        q2 = _pick_requirement_like(c2)
        if not q1 or not q2:
            continue
        question = (
            f"请结合项目「{_short_entity((projects.get(pid) or {}).get('project_name') or '', max_len=16)}」"
            f"的{theme}，说明投标人需要同时注意哪些关键条件？"
        )
        if question_leaks_quote(question, q1) or question_leaks_quote(question, q2):
            continue
        src1 = (docs.get(c1.document_id) or {}).get("source_url")
        src2 = (docs.get(c2.document_id) or {}).get("source_url")
        # Both sources required (document_id/chunk_id/url/page/quote)
        if not src1 or not src2:
            continue
        if not c1.document_id or not c2.document_id:
            continue
        if not c1.page_start or not c2.page_start:
            continue
        path_a = (c1.section_path or "") or f"doc:{c1.document_id}:p{c1.page_start}"
        path_b = (c2.section_path or "") or f"doc:{c2.document_id}:p{c2.page_start}"
        if path_a == path_b:
            continue
        a1 = _answer_from_quote(q1, c1.text)
        a2 = _answer_from_quote(q2, c2.text)
        if not a1 or not a2:
            continue
        # Combined answer must cover both evidence spans with per-part support
        answer = f"{a1.rstrip('。；;')}；另方面，{a2}"
        if not multi_section_dual_answer_ok(answer, q1, q2):
            continue
        questions.append(
            RAGQuestion(
                question_id=str(stable_uuid(f"ragq:multi:{pid}:{c1.chunk_id}:{c2.chunk_id}")),
                project_id=pid,
                question=question,
                answer=answer[:400],
                answerable=True,
                gold_chunk_ids=[c1.chunk_id, c2.chunk_id],
                gold_document_ids=[c1.document_id, c2.document_id],
                source_document_ids=[c1.document_id, c2.document_id],
                source_urls=[src1, src2],
                source_pages=[int(c1.page_start), int(c2.page_start)],
                source_quotes=[q1[:220], q2[:220]],
                question_type=QuestionType.multi_section,
                difficulty=Difficulty.hard,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )
        type_counts[QuestionType.multi_section] += 1
        per_project[pid] += 1
        per_chunk[c1.chunk_id] += 1
        per_chunk[c2.chunk_id] += 1

    # 3) Chunk-backed fill for under-represented types, then any remaining capacity to target
    fill_types = list(TYPE_RATIOS.keys())
    for pass_id in range(2):
        for chunk in chunks_all:
            if sum(1 for q in questions if q.answerable) >= answerable_target:
                break
            pid = chunk.project_id
            proj = projects.get(pid) or {}
            if per_project[pid] >= max_per_project or per_chunk[chunk.chunk_id] >= 2:
                continue
            quote = _pick_requirement_like(chunk)
            if not quote:
                continue
            qtype = None
            if pass_id == 0:
                for qt, cap in type_caps.items():
                    if qt == QuestionType.multi_section:
                        continue
                    if type_counts[qt] < cap:
                        qtype = qt
                        break
                if qtype is None:
                    break
            else:
                # Second pass: ignore soft type caps, rotate types for diversity
                qtype = fill_types[per_chunk[chunk.chunk_id] % len(fill_types)]
                if qtype == QuestionType.multi_section:
                    qtype = QuestionType.qualification
            if proj.get("bundle_level") == "level_c" and qtype == QuestionType.multi_section:
                continue
            # Synthetic req context from chunk for diversity
            faux_req = {"title": quote[:12], "category": qtype.value, "requirement_id": chunk.chunk_id}
            question = _natural_question(qtype, faux_req, quote, proj)
            if not question or question_leaks_quote(question, quote):
                continue
            answer = _answer_from_quote(quote, chunk.text)
            if not answer:
                continue
            if answer not in chunk.text and _norm(answer.rstrip("…")[:40]) not in _norm(chunk.text):
                continue
            src_url = (docs.get(chunk.document_id) or {}).get("source_url")
            if not src_url:
                continue
            key = _norm(question)
            if any(_norm(q.question) == key for q in questions):
                # Add page/code to reduce collision
                question = f"{question.rstrip('？?')}（文件第{chunk.page_start}页）？"
                if question_leaks_quote(question, quote) or any(_norm(q.question) == _norm(question) for q in questions):
                    continue
            questions.append(
                RAGQuestion(
                    question_id=str(
                        stable_uuid(f"ragq:chunk:{chunk.chunk_id}:{qtype.value}:{content_key(quote)}:{pass_id}")
                    ),
                    project_id=pid,
                    question=question,
                    answer=answer,
                    answerable=True,
                    gold_chunk_ids=[chunk.chunk_id],
                    gold_document_ids=[chunk.document_id],
                    source_document_ids=[chunk.document_id],
                    source_urls=[src_url],
                    source_pages=[chunk.page_start],
                    source_quotes=[quote],
                    question_type=qtype,
                    difficulty=Difficulty.medium,
                    quality_level=QualityLevel.silver,
                    review_status=ReviewStatus.pending,
                )
            )
            type_counts[qtype] += 1
            per_project[pid] += 1
            per_chunk[chunk.chunk_id] += 1

    # Dedup answerable by question text; drop evidence failures early
    seen_q: set[str] = set()
    answerable: list[RAGQuestion] = []
    for q in questions:
        if not q.answerable:
            continue
        key = _norm(q.question)
        if key in seen_q or question_leaks_quote(q.question, (q.source_quotes or [""])[0]):
            continue
        quotes = q.source_quotes or []
        ans = q.answer or ""
        if not ans or not quotes:
            continue
        if not any(_norm(ans) in _norm(qq) or fuzz_partial(ans, qq) >= 70 for qq in quotes):
            continue
        # each quote must exist in at least one gold chunk
        ok_quote = True
        for qq in quotes:
            found = False
            for cid in q.gold_chunk_ids:
                ch = chunks_by_id.get(cid)
                if ch and (_norm(qq[:40]) in _norm(ch.text) or qq[:24] in (ch.text or "")):
                    found = True
                    break
            if not found:
                ok_quote = False
                break
        if not ok_quote:
            continue
        seen_q.add(key)
        answerable.append(q)

    # 4) Unanswerable 10-15% after dedup
    # First compute desired band relative to final mixed set.
    # Build candidates then trim to hit [10%, 15%].
    unans: list[RAGQuestion] = []
    for i, pid in enumerate(sorted(projects)):
        corpus = statements_by_project.get(pid, "")
        tmpl = UNANSWERABLE_TEMPLATES[(i * 3) % len(UNANSWERABLE_TEMPLATES)]
        kws = _unanswerable_keywords(tmpl)
        if _corpus_has_any(corpus, kws):
            # try next templates
            found = False
            for off in range(len(UNANSWERABLE_TEMPLATES)):
                cand = UNANSWERABLE_TEMPLATES[(i + off) % len(UNANSWERABLE_TEMPLATES)]
                ck = _unanswerable_keywords(cand)
                if not _corpus_has_any(corpus, ck):
                    tmpl = cand
                    found = True
                    break
            if not found:
                continue
        # Make unanswerable questions unique per project
        qtext = f"就项目「{_short_entity((projects.get(pid) or {}).get('project_name') or '', max_len=16)}」而言，{tmpl}"
        if question_leaks_quote(qtext, ""):
            qtext = tmpl
        key = _norm(qtext)
        if key in seen_q:
            continue
        seen_q.add(key)
        unans.append(
            RAGQuestion(
                question_id=str(stable_uuid(f"ragq:unans:{pid}:{tmpl}")),
                project_id=pid,
                question=qtext,
                answer=None,
                answerable=False,
                gold_chunk_ids=[],
                gold_document_ids=[],
                source_document_ids=[],
                source_urls=[],
                source_pages=[],
                source_quotes=[],
                question_type=QuestionType.unanswerable,
                difficulty=Difficulty.easy,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )

    # Assemble to target size with unanswerable ratio in [0.10, 0.15]
    final: list[RAGQuestion] = []
    # Cap answerable to allow room
    max_ans = int(target * 0.90)
    ans_keep = answerable[:max_ans]
    # Choose unanswerable count
    # Want u/(a+u) in [0.10, 0.15] => u in [a/9, 3a/17]
    a = len(ans_keep)
    lo = max(1, int((a / 0.90) * 0.10))  # approx
    # exact: u >= 0.10*(a+u) => u >= a/9; u <= 0.15*(a+u) => u <= 3a/17
    u_min = max(1, (a + 8) // 9)
    u_max = max(u_min, (3 * a) // 17) if a else 1
    if a == 0:
        u_min, u_max = 1, max(1, min(len(unans), int(target * 0.15)))
    u_take = min(len(unans), max(u_min, min(u_max, len(unans))))
    # If still out of band after taking available, trim answerable
    while a and u_take / (a + u_take) < 0.10 and a > 10:
        a -= 1
        ans_keep = ans_keep[:a]
        u_min = max(1, (a + 8) // 9)
        u_take = min(len(unans), max(u_min, u_take))
    while a and u_take / (a + u_take) > 0.15 and u_take > 1:
        u_take -= 1

    final.extend(ans_keep)
    final.extend(unans[:u_take])
    final = final[:target]

    # Enforce max_project_share <= 10% on FINAL count (iterative quality-priority trim)
    def _trim_project_share(rows: list[RAGQuestion]) -> list[RAGQuestion]:
        if not rows:
            return rows
        type_counts_local = Counter(q.question_type.value for q in rows)
        for _ in range(20):
            n = len(rows)
            if n == 0:
                return rows
            cap = n // 10  # floor(n * 0.10); if 0, share cannot meet 10% → ok fails
            by_pid = Counter(q.project_id for q in rows)
            over = [(pid, cnt) for pid, cnt in by_pid.items() if cnt > max(cap, 0)]
            if not over or cap < 1:
                return rows
            # Drop lowest-priority extras from oversized projects
            pid, cnt = max(over, key=lambda x: x[1])
            drop_n = cnt - cap
            candidates = [q for q in rows if q.project_id == pid]

            def drop_key(q: RAGQuestion) -> tuple:
                tcount = type_counts_local.get(q.question_type.value, 0)
                return (
                    0 if q.quality_level.value == "gold" else 1,
                    0 if (projects.get(q.project_id) or {}).get("bundle_level") in {"level_a", "level_b"} else 1,
                    0 if tcount <= 2 else 1,  # protect scarce types
                    0 if len(q.gold_chunk_ids) >= 2 else 1,
                    q.question_id,
                )

            ranked = sorted(candidates, key=drop_key, reverse=True)
            drop_ids = {q.question_id for q in ranked[:drop_n]}
            # Prefer dropping answerable before collapsing unanswerable band too hard
            rows = [q for q in rows if q.question_id not in drop_ids]
            type_counts_local = Counter(q.question_type.value for q in rows)
        return rows

    final = _trim_project_share(final)
    # Re-balance unanswerable ratio after trim if needed
    if final:
        u_ratio = sum(1 for q in final if not q.answerable) / len(final)
        if u_ratio < 0.10:
            # drop answerable until band or cannot
            ans = [q for q in final if q.answerable]
            una = [q for q in final if not q.answerable]
            while ans and len(una) / (len(ans) + len(una)) < 0.10:
                ans.pop()
            final = _trim_project_share(ans + una)
        elif u_ratio > 0.15:
            ans = [q for q in final if q.answerable]
            una = [q for q in final if not q.answerable]
            while una and len(una) / (len(ans) + len(una)) > 0.15:
                una.pop()
            final = _trim_project_share(ans + una)

    unans_ratio = (sum(1 for q in final if not q.answerable) / len(final)) if final else 0.0
    by_type = Counter(q.question_type.value for q in final)
    max_share = max(Counter(q.project_id for q in final).values(), default=0) / max(len(final), 1)
    leak_n = 0
    for q in final:
        if q.answerable and q.source_quotes and any(question_leaks_quote(q.question, qq) for qq in q.source_quotes):
            leak_n += 1

    type_coverage_ok = sum(1 for t in TYPE_RATIOS if by_type.get(t.value, 0) > 0) >= 6
    ok_band = 0.10 <= unans_ratio <= 0.15 if final else False
    ok_share = max_share <= 0.10 + 1e-9 if final else False

    multi_failed: list[dict[str, str]] = []
    multi_total = 0
    multi_dual_chunk = 0
    multi_dual_source = 0
    multi_dual_answer = 0
    chunk_lookup = {c.chunk_id: c for c in chunks_all}
    for q in final:
        if q.question_type != QuestionType.multi_section:
            continue
        multi_total += 1
        cids = q.gold_chunk_ids or []
        quotes = q.source_quotes or []
        urls = q.source_urls or []
        pages = q.source_pages or []
        docs_ids = q.source_document_ids or []
        reasons: list[str] = []
        if len(set(cids)) >= 2:
            multi_dual_chunk += 1
        else:
            reasons.append("need_two_distinct_chunks")
        paths = []
        for cid in cids[:2]:
            ch = chunk_lookup.get(cid)
            if ch:
                paths.append((ch.section_path or "") or f"doc:{ch.document_id}:p{ch.page_start}")
        if len(paths) == 2 and paths[0] == paths[1]:
            reasons.append("same_section_path")
        if (
            len(urls) >= 2
            and all(urls[:2])
            and len(docs_ids) >= 2
            and all(docs_ids[:2])
            and len(pages) >= 2
            and all(pages[:2])
            and len(quotes) >= 2
            and all(q.strip() for q in quotes[:2])
        ):
            multi_dual_source += 1
        else:
            reasons.append("missing_second_source_fields")
        if len(quotes) >= 2 and multi_section_dual_answer_ok(q.answer or "", quotes[0], quotes[1]):
            multi_dual_answer += 1
        else:
            reasons.append("answer_not_dual_supported")
        if reasons:
            multi_failed.append({"question_id": q.question_id, "reason": ";".join(reasons)})

    ok_multi = multi_total == 0 or (
        multi_dual_chunk == multi_total and multi_dual_source == multi_total and multi_dual_answer == multi_total
    )
    ok = bool(final) and leak_n == 0 and ok_band and ok_share and type_coverage_ok and ok_multi

    quality_report = {
        "questions": len(final),
        "answerable": sum(1 for q in final if q.answerable),
        "unanswerable": sum(1 for q in final if not q.answerable),
        "unanswerable_ratio": unans_ratio,
        "by_type": dict(by_type),
        "max_project_share": max_share,
        "leaky_questions": leak_n,
        "dry_run": dry_run,
        "target": target,
        "ok_unanswerable_band": ok_band,
        "ok_max_project_share": ok_share,
        "ok_type_coverage": type_coverage_ok,
        "multi_section_total": multi_total,
        "multi_section_dual_chunk_pass": multi_dual_chunk,
        "multi_section_dual_source_pass": multi_dual_source,
        "multi_section_dual_answer_pass": multi_dual_answer,
        "failed_question_ids": multi_failed[:50],
        "ok_multi_section": ok_multi,
        "ok": ok,
    }

    stats = {**quality_report}
    if not dry_run:
        write_jsonl(ensure_dir(settings.datasets_root / "eval" / "rag") / "questions.jsonl", final)
        write_json(ensure_dir(settings.datasets_root / "reports") / "rag_quality_report.json", quality_report)
    log_stats(log, "rag_eval", {k: stats[k] for k in ("questions", "answerable", "unanswerable", "unanswerable_ratio", "max_project_share")})
    return stats


def content_key(text: str) -> str:
    from bidpilot_data.utils.hashing import content_fingerprint

    return content_fingerprint(text)
