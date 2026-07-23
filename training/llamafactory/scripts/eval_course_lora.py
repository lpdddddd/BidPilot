#!/usr/bin/env python3
"""Evaluate base vs LoRA adapter on course_pilot test ShareGPT samples.

Uses transformers+peft locally (does not require vLLM LoRA). Writes a JSON report.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def _load_cases(path: Path, limit: int) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data[:limit]


def _prompt_from_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    system = ""
    user_parts: list[str] = []
    gold = ""
    for m in messages:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "system":
            system = content
        elif role == "user":
            user_parts.append(content)
        elif role == "assistant":
            gold = content
    return (system + "\n\n" + "\n".join(user_parts)).strip(), gold


def _json_ok(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except Exception:
        return False


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("{"):
        return text
    m = re.search(r"\{[\s\S]*\}", text)
    return m.group(0) if m else text


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
    json_ok = 0
    for i, case in enumerate(cases):
        prompt, gold = _prompt_from_messages(case.get("messages") or [])
        messages = []
        # Prefer chat template
        sys_msg = None
        user_content = prompt
        for m in case.get("messages") or []:
            if m.get("role") == "system":
                sys_msg = m.get("content")
            elif m.get("role") == "user":
                user_content = m.get("content")
                break
        chat = []
        if sys_msg:
            chat.append({"role": "system", "content": sys_msg})
        chat.append({"role": "user", "content": user_content})
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
        ok = _json_ok(gen_json)
        json_ok += int(ok)
        gold_obj = None
        try:
            gold_obj = json.loads(gold) if gold.strip().startswith("{") else None
        except Exception:
            gold_obj = None
        pred_obj = None
        if ok:
            try:
                pred_obj = json.loads(gen_json)
            except Exception:
                pred_obj = None
        key_overlap = None
        if isinstance(gold_obj, dict) and isinstance(pred_obj, dict):
            gk, pk = set(gold_obj), set(pred_obj)
            key_overlap = (len(gk & pk) / len(gk)) if gk else None
        rows.append(
            {
                "idx": i,
                "task_type": case.get("task_type"),
                "json_ok": ok,
                "key_overlap": key_overlap,
                "latency_ms": round(latency, 1),
                "pred_preview": gen[:240],
            }
        )
    return {
        "n": len(cases),
        "json_ok_rate": (json_ok / len(cases)) if cases else 0.0,
        "mean_key_overlap": (
            sum(r["key_overlap"] for r in rows if r["key_overlap"] is not None)
            / max(1, sum(1 for r in rows if r["key_overlap"] is not None))
        ),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", default="/root/autodl-tmp/models/Qwen3-8B")
    parser.add_argument("--adapter-path", default="")
    parser.add_argument(
        "--test-file",
        default=str(ROOT / "training/llamafactory/data/bidpilot_course_pilot_test.json"),
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--device", default="cuda:2")
    parser.add_argument(
        "--report",
        default=str(ROOT / "training/llamafactory/outputs/course_eval_report.json"),
    )
    args = parser.parse_args()
    cases = _load_cases(Path(args.test_file), args.limit)
    base = run_eval(
        model_path=args.model_path,
        adapter_path=None,
        cases=cases,
        max_new_tokens=args.max_new_tokens,
        device=args.device,
    )
    tuned = None
    if args.adapter_path:
        tuned = run_eval(
            model_path=args.model_path,
            adapter_path=args.adapter_path,
            cases=cases,
            max_new_tokens=args.max_new_tokens,
            device=args.device,
        )
    report = {
        "model_path": args.model_path,
        "adapter_path": args.adapter_path or None,
        "n": len(cases),
        "base": {k: v for k, v in base.items() if k != "rows"},
        "lora": ({k: v for k, v in tuned.items() if k != "rows"} if tuned else None),
        "base_rows": base["rows"],
        "lora_rows": (tuned["rows"] if tuned else None),
    }
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("n", "base", "lora")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
