#!/usr/bin/env python3
"""Offline Base vs Course LoRA eval on fixed course_pilot test split.

Deterministic sample (seed), structure + field-value metrics where reference supports them.
Does not log full prompts. Writes JSON summary + Markdown report under datasets/reports/.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def _load_sharegpt(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"expected list in {path}")
    return data


def _sample(cases: list[dict[str, Any]], *, limit: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    indexed = list(enumerate(cases))
    rng.shuffle(indexed)
    picked = sorted(indexed[:limit], key=lambda x: x[0])
    return [c for _, c in picked]


def _messages_parts(messages: list[dict[str, Any]]) -> tuple[str | None, str, str]:
    system = None
    user = ""
    gold = ""
    for m in messages:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "system" and system is None:
            system = content
        elif role == "user" and not user:
            user = content
        elif role == "assistant" and not gold:
            gold = content
    return system, user, gold


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("{"):
        return text
    m = re.search(r"\{[\s\S]*\}", text)
    return m.group(0) if m else text


def _json_loads(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def _schema_valid(obj: Any) -> bool:
    return isinstance(obj, dict) and len(obj) > 0


def _required_field_coverage(gold: dict[str, Any], pred: dict[str, Any] | None) -> float | None:
    if not gold:
        return None
    if not isinstance(pred, dict):
        return 0.0
    keys = list(gold.keys())
    if not keys:
        return None
    return sum(1 for k in keys if k in pred) / len(keys)


def _norm(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v).strip().lower()


def _field_exact_match(gold: dict[str, Any], pred: dict[str, Any] | None) -> float | None:
    if not gold:
        return None
    if not isinstance(pred, dict):
        return 0.0
    keys = list(gold.keys())
    if not keys:
        return None
    return sum(1 for k in keys if _norm(pred.get(k)) == _norm(gold.get(k))) / len(keys)


def _verdict_accuracy(gold: dict[str, Any], pred: dict[str, Any] | None) -> float | None:
    for key in ("verdict", "label", "risk_level", "category", "answerable"):
        if key in gold:
            if not isinstance(pred, dict):
                return 0.0
            return 1.0 if _norm(pred.get(key)) == _norm(gold.get(key)) else 0.0
    return None  # N/A for this sample


def _evidence_support_rate(gold: dict[str, Any], pred: dict[str, Any] | None) -> float | None:
    # Only when gold exposes evidence/citations fields.
    g_ev = gold.get("evidence") or gold.get("citations") or gold.get("evidence_ids")
    if g_ev is None:
        return None
    if not isinstance(pred, dict):
        return 0.0
    p_ev = pred.get("evidence") or pred.get("citations") or pred.get("evidence_ids")
    if p_ev is None:
        return 0.0
    return 1.0 if _norm(p_ev) == _norm(g_ev) else 0.0


def _citation_validity(gold: dict[str, Any], pred: dict[str, Any] | None) -> float | None:
    g_c = gold.get("citations")
    if g_c is None:
        return None
    if not isinstance(pred, dict):
        return 0.0
    p_c = pred.get("citations")
    if p_c is None:
        return 0.0
    if isinstance(g_c, list) and isinstance(p_c, list):
        gs, ps = {_norm(x) for x in g_c}, {_norm(x) for x in p_c}
        if not gs:
            return None
        return len(gs & ps) / len(gs)
    return 1.0 if _norm(p_c) == _norm(g_c) else 0.0


def run_eval(
    *,
    model_path: str,
    adapter_path: str | None,
    cases: list[dict[str, Any]],
    max_new_tokens: int,
    device: str,
) -> dict[str, Any]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=torch.bfloat16,
        device_map=device,
        trust_remote_code=True,
    )
    if adapter_path:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()

    rows: list[dict[str, Any]] = []
    for i, case in enumerate(cases):
        system, user, gold = _messages_parts(case.get("messages") or [])
        chat: list[dict[str, str]] = []
        if system:
            chat.append({"role": "system", "content": system})
        chat.append({"role": "user", "content": user})
        text = tok.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        inputs = tok(text, return_tensors="pt").to(model.device)
        t0 = time.perf_counter()
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tok.eos_token_id,
            )
        latency = (time.perf_counter() - t0) * 1000
        gen = tok.decode(out[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True)
        gen_json = _extract_json(gen)
        pred_obj = _json_loads(gen_json)
        gold_obj = _json_loads(gold) if gold.strip().startswith("{") else None
        json_ok = pred_obj is not None
        schema_ok = _schema_valid(pred_obj) if json_ok else False
        row = {
            "idx": i,
            "source_index": case.get("_source_index"),
            "task_type": case.get("task_type"),
            "json_ok": json_ok,
            "schema_valid": schema_ok,
            "required_field_coverage": (
                _required_field_coverage(gold_obj, pred_obj) if isinstance(gold_obj, dict) else None
            ),
            "verdict_accuracy": (
                _verdict_accuracy(gold_obj, pred_obj) if isinstance(gold_obj, dict) else None
            ),
            "field_exact_match": (
                _field_exact_match(gold_obj, pred_obj) if isinstance(gold_obj, dict) else None
            ),
            "evidence_support": (
                _evidence_support_rate(gold_obj, pred_obj) if isinstance(gold_obj, dict) else None
            ),
            "citation_validity": (
                _citation_validity(gold_obj, pred_obj) if isinstance(gold_obj, dict) else None
            ),
            "latency_ms": round(latency, 1),
            "pred_preview": gen[:200].replace("\n", " "),
            "error_tags": [],
        }
        if not json_ok:
            row["error_tags"].append("format_error")
        elif isinstance(gold_obj, dict) and row["field_exact_match"] is not None and row["field_exact_match"] < 1.0:
            row["error_tags"].append("field_value_error")
        if len(gen) > 1200:
            row["error_tags"].append("overlong")
        rows.append(row)

    def _mean(key: str) -> float | None:
        vals = [r[key] for r in rows if r.get(key) is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)

    by_task: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(r.get("task_type") or "unknown")].append(r)
    for task, gro in groups.items():
        def gmean(key: str, g: list[dict[str, Any]] = gro) -> float | None:
            vals = [x[key] for x in g if x.get(key) is not None]
            return (sum(vals) / len(vals)) if vals else None

        by_task[task] = {
            "n": len(gro),
            "json_ok_rate": sum(1 for x in gro if x["json_ok"]) / len(gro),
            "schema_validity": sum(1 for x in gro if x["schema_valid"]) / len(gro),
            "required_field_coverage": gmean("required_field_coverage"),
            "verdict_accuracy": gmean("verdict_accuracy"),
            "field_exact_match": gmean("field_exact_match"),
            "evidence_support": gmean("evidence_support"),
            "citation_validity": gmean("citation_validity"),
            "avg_latency_ms": gmean("latency_ms"),
        }

    return {
        "n": len(cases),
        "json_ok_rate": sum(1 for r in rows if r["json_ok"]) / max(1, len(rows)),
        "schema_validity": sum(1 for r in rows if r["schema_valid"]) / max(1, len(rows)),
        "required_field_coverage": _mean("required_field_coverage"),
        "verdict_accuracy": _mean("verdict_accuracy"),
        "field_exact_match": _mean("field_exact_match"),
        "evidence_support": _mean("evidence_support"),
        "citation_validity": _mean("citation_validity"),
        "avg_latency_ms": _mean("latency_ms"),
        "failed_cases": sum(1 for r in rows if not r["json_ok"]),
        "by_task": by_task,
        "rows": rows,
    }


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return b - a


def _fmt(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:.4f}"


def write_markdown(summary: dict[str, Any], path: Path) -> None:
    base = summary["base"]
    lora = summary["lora"]
    rows = [
        ("JSON parse rate", base["json_ok_rate"], lora["json_ok_rate"]),
        ("Schema validity", base["schema_validity"], lora["schema_validity"]),
        ("Required field coverage", base["required_field_coverage"], lora["required_field_coverage"]),
        ("Verdict accuracy", base["verdict_accuracy"], lora["verdict_accuracy"]),
        ("Field-level accuracy", base["field_exact_match"], lora["field_exact_match"]),
        ("Evidence support", base["evidence_support"], lora["evidence_support"]),
        ("Citation validity", base["citation_validity"], lora["citation_validity"]),
        ("Average latency (ms)", base["avg_latency_ms"], lora["avg_latency_ms"]),
        ("Failed cases", float(base["failed_cases"]), float(lora["failed_cases"])),
    ]
    lines = [
        "# Course LoRA offline eval (Step 14)",
        "",
        f"- seed: `{summary['seed']}`",
        f"- n: `{summary['n']}` (fixed test split sample)",
        f"- adapter: `{summary.get('adapter_path')}`",
        "",
        "| 指标 | Base | LoRA | 变化 |",
        "|---|---:|---:|---:|",
    ]
    for name, b, l in rows:
        d = _delta(b, l)
        lines.append(f"| {name} | {_fmt(b)} | {_fmt(l)} | {_fmt(d)} |")
    lines += [
        "",
        "## Honest conclusion",
        "",
        summary.get("conclusion") or "",
        "",
        "## Spot checks (first 5 paired rows)",
        "",
    ]
    for i, pair in enumerate(summary.get("spot_checks") or []):
        lines.append(f"### Sample {i + 1} task={pair.get('task_type')}")
        lines.append(f"- base tags: {pair.get('base_error_tags')}")
        lines.append(f"- lora tags: {pair.get('lora_error_tags')}")
        lines.append(f"- base preview: `{pair.get('base_preview')}`")
        lines.append(f"- lora preview: `{pair.get('lora_preview')}`")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", default="/root/autodl-tmp/models/Qwen3-8B")
    parser.add_argument(
        "--adapter-path",
        default=str(ROOT / "training/llamafactory/outputs/qwen3_8b_lora_course"),
    )
    parser.add_argument(
        "--test-file",
        default=str(ROOT / "training/llamafactory/data/bidpilot_course_pilot_test.json"),
    )
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--seed", type=int, default=14)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--device", default="cuda:3")
    parser.add_argument(
        "--report-json",
        default=str(ROOT / "datasets/reports/course_lora_eval_summary.json"),
    )
    parser.add_argument(
        "--report-md",
        default=str(ROOT / "datasets/reports/course_lora_eval_report.md"),
    )
    parser.add_argument(
        "--raw-json",
        default=str(ROOT / "training/llamafactory/outputs/course_eval_report.json"),
    )
    args = parser.parse_args()

    all_cases = _load_sharegpt(Path(args.test_file))
    for i, c in enumerate(all_cases):
        c["_source_index"] = i
    cases = _sample(all_cases, limit=min(args.limit, len(all_cases)), seed=args.seed)

    base = run_eval(
        model_path=args.model_path,
        adapter_path=None,
        cases=cases,
        max_new_tokens=args.max_new_tokens,
        device=args.device,
    )
    tuned = run_eval(
        model_path=args.model_path,
        adapter_path=args.adapter_path,
        cases=cases,
        max_new_tokens=args.max_new_tokens,
        device=args.device,
    )

    spot = []
    for i in range(min(5, len(base["rows"]))):
        br, lr = base["rows"][i], tuned["rows"][i]
        spot.append(
            {
                "task_type": br.get("task_type"),
                "base_error_tags": br.get("error_tags"),
                "lora_error_tags": lr.get("error_tags"),
                "base_preview": br.get("pred_preview"),
                "lora_preview": lr.get("pred_preview"),
            }
        )

    # Honest conclusion from metrics (structure vs field values).
    structure_gain = (tuned["json_ok_rate"] - base["json_ok_rate"]) >= 0.2
    field_na = tuned["field_exact_match"] is None and tuned["verdict_accuracy"] is None
    field_gain = False
    if tuned["field_exact_match"] is not None and base["field_exact_match"] is not None:
        field_gain = (tuned["field_exact_match"] - base["field_exact_match"]) >= 0.05
    if structure_gain and not field_gain:
        conclusion = (
            "LoRA mainly improves structured JSON / schema compliance versus base "
            "(base often emits non-JSON thinking prose). Field-value accuracy gains are "
            "limited or N/A on this silver course_pilot test sample — do not claim domain "
            "judgment uplift without human_gold."
        )
    elif field_gain:
        conclusion = (
            "LoRA improves both structure and some field-level exact match on this sample; "
            "still course_pilot (not human_gold)."
        )
    else:
        conclusion = "See table; interpret carefully — course_pilot automatic QC track."

    summary = {
        "seed": args.seed,
        "n": len(cases),
        "test_file": "training/llamafactory/data/bidpilot_course_pilot_test.json",
        "adapter_path": "training/llamafactory/outputs/qwen3_8b_lora_course",
        "base": {k: v for k, v in base.items() if k != "rows"},
        "lora": {k: v for k, v in tuned.items() if k != "rows"},
        "spot_checks": spot,
        "conclusion": conclusion,
        "note": "key_overlap is NOT used as field accuracy. N/A means reference lacks the field.",
    }
    raw = {
        **summary,
        "base_rows": base["rows"],
        "lora_rows": tuned["rows"],
    }
    Path(args.report_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_json).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(summary, Path(args.report_md))
    Path(args.raw_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.raw_json).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: summary[k] for k in ("seed", "n", "base", "lora", "conclusion")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
