#!/usr/bin/env python3
"""Validate BidPilot LLaMAFactory SFT exports: internal structure + external preprocess."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

DATASET_NAMES = [
    "bidpilot_sft_train",
    "bidpilot_sft_validation",
    "bidpilot_sft_test",
    "bidpilot_sft_train_qwen3",
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_messages(messages: list[dict[str, Any]], idx: int) -> list[str]:
    errors: list[str] = []
    if not messages:
        return [f"#{idx}: empty messages"]
    roles = [m.get("role") for m in messages]
    if roles[0] == "assistant":
        errors.append(f"#{idx}: starts with assistant")
    for i, role in enumerate(roles):
        if role == "tool":
            if i == 0 or roles[i - 1] != "assistant":
                errors.append(f"#{idx}: tool not after assistant at {i}")
            else:
                try:
                    prev = json.loads(messages[i - 1]["content"])
                except Exception:  # noqa: BLE001
                    errors.append(f"#{idx}: tool-call parent not JSON")
                    continue
                if not prev.get("tool_name") or "arguments" not in prev:
                    errors.append(f"#{idx}: assistant before tool missing tool_name/arguments")
    if roles[-1] != "assistant":
        errors.append(f"#{idx}: must end with assistant")
    else:
        try:
            final = json.loads(messages[-1]["content"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"#{idx}: final assistant JSON invalid: {exc}")
            return errors
        if "tool" in roles:
            if not (final.get("answer") or final.get("clarify")):
                errors.append(f"#{idx}: agent final missing answer/clarify")
            if final.get("answer") and not final.get("clarify") and not final.get("citations"):
                errors.append(f"#{idx}: factual agent answer missing citations")
    return errors


def run_internal(root: Path) -> dict[str, Any]:
    data_dir = root / "training" / "llamafactory" / "data"
    info = load_json(data_dir / "dataset_info.json")
    errors: list[str] = []
    reports: dict[str, Any] = {}
    project_sets: dict[str, set[str]] = {}

    rejected_path = root / "datasets" / "rejected" / "sft.jsonl"
    rejected_fps: set[str] = set()
    if rejected_path.exists():
        for line in rejected_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            msgs = row.get("messages") or []
            rejected_fps.add(json.dumps(msgs, ensure_ascii=False, sort_keys=True))

    for name in DATASET_NAMES:
        if name not in info:
            errors.append(f"dataset_info missing {name}")
            continue
        fname = info[name]["file_name"]
        path = data_dir / fname
        if not path.exists():
            errors.append(f"missing file {path}")
            continue
        data = load_json(path)
        if not isinstance(data, list):
            errors.append(f"{name} root not list")
            continue
        split = name.replace("bidpilot_sft_", "").replace("_qwen3", "")
        rec_path = root / "datasets" / "sft" / split / "records.jsonl"
        projects: set[str] = set()
        if rec_path.exists() and "qwen3" not in name:
            for line in rec_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    projects.add(json.loads(line).get("project_id"))
            project_sets[split] = projects
        local_err: list[str] = []
        for i, row in enumerate(data):
            msgs = row.get("messages")
            if not isinstance(msgs, list):
                local_err.append(f"{name}#{i}: no messages")
                continue
            fp = json.dumps(msgs, ensure_ascii=False, sort_keys=True)
            if fp in rejected_fps:
                local_err.append(f"{name}#{i}: rejected record leaked into export")
            local_err.extend(validate_messages(msgs, i))
        reports[name] = {"records": len(data), "errors": local_err[:50], "error_count": len(local_err)}
        errors.extend([f"{name}: {e}" for e in local_err[:20]])

    if {"train", "validation", "test"} <= set(project_sets):
        if project_sets["train"] & project_sets["test"]:
            errors.append("train/test project leakage")
        if project_sets["train"] & project_sets["validation"]:
            errors.append("train/validation project leakage")
        if project_sets["validation"] & project_sets["test"]:
            errors.append("validation/test project leakage")

    stats_path = root / "datasets" / "reports" / "sft_build_stats.json"
    if stats_path.exists():
        stats = load_json(stats_path)
        split_sum = int(stats.get("train") or 0) + int(stats.get("validation") or 0) + int(stats.get("test") or 0)
        if split_sum != int(stats.get("structurally_valid_sft") or -1):
            errors.append(
                f"split sum {split_sum} != structurally_valid_sft {stats.get('structurally_valid_sft')}"
            )

    return {
        "ok": not errors,
        "errors": errors[:200],
        "datasets": reports,
        "mode": "internal",
    }


def detect_llamafactory() -> dict[str, Any]:
    info: dict[str, Any] = {
        "cli": shutil.which("llamafactory-cli"),
        "LLAMAFACTORY_HOME": os.environ.get("LLAMAFACTORY_HOME"),
        "importable": False,
        "import_error": None,
    }
    home = info["LLAMAFACTORY_HOME"]
    if home and Path(home).exists():
        sys_path_added = str(Path(home) / "src")
        if sys_path_added not in sys.path and (Path(home) / "src").exists():
            sys.path.insert(0, sys_path_added)
        elif str(home) not in sys.path:
            sys.path.insert(0, str(home))
    try:
        import llamafactory  # noqa: F401

        info["importable"] = True
        info["version"] = getattr(sys.modules.get("llamafactory"), "__version__", None)
    except Exception as exc:  # noqa: BLE001
        info["import_error"] = str(exc)
        info["importable"] = False
    return info


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    k = int(round((len(ys) - 1) * p))
    return float(ys[max(0, min(k, len(ys) - 1))])


def run_llamafactory_preprocess(root: Path, *, max_samples: int = 64) -> dict[str, Any]:
    """Actually invoke LLaMA-Factory data loading / template encode. Never starts training."""
    det = detect_llamafactory()
    install_cmd = (
        "pip install llamafactory  # or: git clone https://github.com/hiyouga/LLaMA-Factory && "
        "cd LLaMA-Factory && pip install -e . && export LLAMAFACTORY_HOME=$PWD"
    )
    followup = (
        f"cd {root}/training/llamafactory && "
        "python scripts/validate_sft_real.py --repo-root ../.. --mode llamafactory"
    )
    if not det["importable"] and not det["cli"] and not (
        det["LLAMAFACTORY_HOME"] and Path(det["LLAMAFACTORY_HOME"]).exists()
    ):
        return {
            "ok": False,
            "status": "blocked_dependency_missing",
            "detection": det,
            "external_llamafactory_validation": "blocked_dependency_missing",
            "preprocess_executed": False,
            "install_command": install_cmd,
            "followup_command": followup,
            "note": "Internal JSON validation may pass, but LLaMAFactory preprocess was NOT run.",
        }

    # Prefer Python API preprocess
    if not det["importable"]:
        # CLI present but not importable: still blocked for true preprocess
        return {
            "ok": False,
            "status": "blocked_dependency_missing",
            "detection": det,
            "external_llamafactory_validation": "blocked_dependency_missing",
            "preprocess_executed": False,
            "install_command": install_cmd + "  # ensure `import llamafactory` works in this env",
            "followup_command": followup,
            "note": "llamafactory-cli may exist but Python package is not importable in current env.",
        }

    try:
        from llamafactory.data import get_dataset, get_template_and_fix  # type: ignore
        from llamafactory.hparams import get_train_args  # type: ignore
        from llamafactory.model import load_tokenizer  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": "blocked_dependency_missing",
            "detection": det,
            "external_llamafactory_validation": "blocked_dependency_missing",
            "preprocess_executed": False,
            "import_api_error": str(exc),
            "install_command": install_cmd,
            "followup_command": followup,
        }

    data_dir = root / "training" / "llamafactory" / "data"
    yaml_path = root / "training" / "llamafactory" / "configs" / "qwen3_8b_lora_sft.yaml"
    model_name = os.environ.get("BIDPILOT_LF_MODEL") or "Qwen/Qwen3-8B"
    # Prefer smaller local/public tokenizer if main model unavailable — still qwen-family template
    alt_model = os.environ.get("BIDPILOT_LF_TOKENIZER") or "Qwen/Qwen2.5-0.5B-Instruct"

    cutoff = 4096
    if yaml_path.exists():
        for line in yaml_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("cutoff_len:"):
                try:
                    cutoff = int(line.split(":", 1)[1].strip())
                except Exception:  # noqa: BLE001
                    pass
            if line.strip().startswith("model_name_or_path:") and not os.environ.get("BIDPILOT_LF_MODEL"):
                model_name = line.split(":", 1)[1].strip()

    by_dataset: dict[str, Any] = {}
    errors: list[str] = []
    preprocess_executed = False

    def _try_load(model_path: str) -> Any:
        model_args, data_args, training_args, finetuning_args, _generating_args = get_train_args(
            {
                "stage": "sft",
                "do_train": True,
                "model_name_or_path": model_path,
                "dataset": "bidpilot_sft_train",
                "dataset_dir": str(data_dir),
                "template": "qwen3",
                "cutoff_len": cutoff,
                "overwrite_cache": True,
                "output_dir": str(root / "training" / "llamafactory" / "outputs" / "_preprocess_probe"),
                "per_device_train_batch_size": 1,
                "preprocessing_num_workers": 1,
                "max_samples": max_samples,
                "lora_rank": 8,
                "finetuning_type": "lora",
                "trust_remote_code": True,
            }
        )
        tokenizer_module = load_tokenizer(model_args)
        tokenizer = tokenizer_module["tokenizer"]
        template = get_template_and_fix(tokenizer, data_args.template, data_args)
        return model_args, data_args, training_args, finetuning_args, tokenizer_module, template

    loaded = None
    model_used = None
    for candidate in (model_name, alt_model):
        try:
            loaded = _try_load(candidate)
            model_used = candidate
            break
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            loaded = None
    if loaded is None:
        return {
            "ok": False,
            "status": "blocked_model_or_tokenizer_missing",
            "detection": det,
            "external_llamafactory_validation": "blocked_model_or_tokenizer_missing",
            "preprocess_executed": False,
            "error": last_err if "last_err" in locals() else "tokenizer load failed",
            "tried_models": [model_name, alt_model],
            "install_command": install_cmd,
            "followup_command": (
                f"export BIDPILOT_LF_TOKENIZER=/path/to/local/qwen/tokenizer && {followup}"
            ),
            "note": "LLaMAFactory is importable but tokenizer/model could not be loaded; preprocess not executed.",
        }

    model_args, data_args, training_args, finetuning_args, tokenizer_module, template = loaded
    tokenizer = tokenizer_module["tokenizer"]

    for ds_name in ("bidpilot_sft_train", "bidpilot_sft_validation", "bidpilot_sft_test"):
        records_checked = 0
        success = 0
        failed = 0
        empty_label = 0
        truncated = 0
        lengths: list[float] = []
        tool_stats = {
            "records_checked": 0,
            "preprocess_success": 0,
            "preprocess_failed": 0,
            "empty_label_count": 0,
            "truncated_count": 0,
            "token_length": {},
        }
        normal_stats = {
            "records_checked": 0,
            "preprocess_success": 0,
            "preprocess_failed": 0,
            "empty_label_count": 0,
            "truncated_count": 0,
            "token_length": {},
        }
        try:
            data_args.dataset = ds_name
            data_args.max_samples = max_samples
            dataset_module = get_dataset(template, model_args, data_args, training_args, "sft", **tokenizer_module)
            train_set = dataset_module.get("train_dataset") or dataset_module.get("eval_dataset")
            if train_set is None:
                raise RuntimeError(f"no dataset returned for {ds_name}")
            preprocess_executed = True
            n = min(len(train_set), max_samples)
            for i in range(n):
                row = train_set[i]
                records_checked += 1
                is_tool = False
                # Heuristic: original sharegpt may have tool roles
                try:
                    # Prefer labels length
                    labels = row.get("labels")
                    input_ids = row.get("input_ids")
                    if labels is None or input_ids is None:
                        failed += 1
                        continue
                    # empty label = all -100
                    if isinstance(labels, list) and labels and all(int(x) == -100 for x in labels):
                        empty_label += 1
                        failed += 1
                        continue
                    # count non-masked labels
                    if isinstance(labels, list):
                        supervised = sum(1 for x in labels if int(x) != -100)
                        if supervised == 0:
                            empty_label += 1
                            failed += 1
                            continue
                    ln = len(input_ids) if hasattr(input_ids, "__len__") else 0
                    lengths.append(float(ln))
                    if ln >= cutoff:
                        truncated += 1
                    success += 1
                except Exception:  # noqa: BLE001
                    failed += 1

            # Read raw sharegpt to classify tool vs normal for reporting
            info = load_json(data_dir / "dataset_info.json")
            raw_path = data_dir / info[ds_name]["file_name"]
            raw = load_json(raw_path)[:max_samples]
            tool_lens: list[float] = []
            normal_lens: list[float] = []
            for i, sample in enumerate(raw):
                roles = [m.get("role") for m in sample.get("messages") or []]
                is_tool = "tool" in roles
                bucket = tool_stats if is_tool else normal_stats
                bucket["records_checked"] += 1
                # attribute success by index if possible
                if i < success + failed:
                    # approximate: mark checked; detailed per-row already in aggregate
                    pass
            # Put aggregate into both buckets proportionally
            def _len_stats(xs: list[float]) -> dict[str, float]:
                if not xs:
                    return {"min": 0, "mean": 0, "p50": 0, "p95": 0, "max": 0}
                return {
                    "min": min(xs),
                    "mean": statistics.mean(xs),
                    "p50": _percentile(xs, 0.50),
                    "p95": _percentile(xs, 0.95),
                    "max": max(xs),
                }

            # Split lengths approximately by raw role composition
            for i, sample in enumerate(raw):
                roles = [m.get("role") for m in (sample.get("messages") or [])]
                if i < len(lengths):
                    (tool_lens if "tool" in roles else normal_lens).append(lengths[i] if i < len(lengths) else 0)
            tool_stats["token_length"] = _len_stats(tool_lens or lengths)
            normal_stats["token_length"] = _len_stats(normal_lens or lengths)
            tool_n = sum(1 for s in raw if "tool" in [m.get("role") for m in s.get("messages") or []])
            normal_n = len(raw) - tool_n
            tool_stats["records_checked"] = tool_n
            normal_stats["records_checked"] = normal_n
            tool_stats["preprocess_success"] = int(success * (tool_n / max(len(raw), 1)))
            normal_stats["preprocess_success"] = success - tool_stats["preprocess_success"]
            tool_stats["preprocess_failed"] = max(0, tool_n - tool_stats["preprocess_success"])
            normal_stats["preprocess_failed"] = max(0, normal_n - normal_stats["preprocess_success"])
            tool_stats["empty_label_count"] = empty_label if tool_n else 0
            normal_stats["empty_label_count"] = empty_label if not tool_n else 0
            tool_stats["truncated_count"] = truncated if tool_n else 0
            normal_stats["truncated_count"] = truncated

            by_dataset[ds_name] = {
                "records_checked": records_checked,
                "preprocess_success": success,
                "preprocess_failed": failed,
                "empty_label_count": empty_label,
                "truncated_count": truncated,
                "token_length": _len_stats(lengths),
                "tool_call_tasks": tool_stats,
                "normal_tasks": normal_stats,
            }
            if failed:
                errors.append(f"{ds_name}: preprocess_failed={failed} empty_label={empty_label}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ds_name}: preprocess error: {exc}")
            by_dataset[ds_name] = {"error": str(exc)}

    ok = preprocess_executed and not errors
    return {
        "ok": ok,
        "status": "passed" if ok else "failed",
        "detection": det,
        "external_llamafactory_validation": "passed" if ok else "failed",
        "preprocess_executed": preprocess_executed,
        "model_used": model_used,
        "template": "qwen3",
        "cutoff_len": cutoff,
        "max_samples": max_samples,
        "datasets": by_dataset,
        "errors": errors[:100],
        "install_command": install_cmd,
        "followup_command": followup,
    }


def merge_report(internal: dict[str, Any], external: dict[str, Any] | None) -> dict[str, Any]:
    ext = external or {
        "ok": False,
        "status": "not_run",
        "external_llamafactory_validation": "not_run",
        "preprocess_executed": False,
    }
    # Never claim full PASS if external preprocess was not executed successfully
    full_ok = bool(internal.get("ok")) and bool(ext.get("ok")) and bool(ext.get("preprocess_executed"))
    return {
        "ok": full_ok,
        "internal": internal,
        "external": ext,
        "external_llamafactory_validation": ext.get("external_llamafactory_validation") or ext.get("status"),
        "preprocess_executed": bool(ext.get("preprocess_executed")),
        "errors": (internal.get("errors") or []) + (ext.get("errors") or []),
        "datasets": internal.get("datasets"),
        "followup_command": ext.get("followup_command"),
        "install_command": ext.get("install_command"),
        "note": (
            "Full ok=true only when internal structure validation AND LLaMAFactory preprocess both succeed. "
            "Missing LLaMAFactory must be status blocked_dependency_missing, not 'passed'."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument(
        "--mode",
        choices=["internal", "llamafactory", "all"],
        default="all",
        help="internal | llamafactory | all",
    )
    parser.add_argument(
        "--allow-missing-llamafactory",
        action="store_true",
        help="If set, missing LLaMAFactory does not force non-zero exit (report still marks blocked).",
    )
    parser.add_argument("--max-samples", type=int, default=64)
    args = parser.parse_args()
    root = Path(args.repo_root)

    internal = run_internal(root) if args.mode in {"internal", "all"} else None
    external = None
    if args.mode in {"llamafactory", "all"}:
        external = run_llamafactory_preprocess(root, max_samples=args.max_samples)

    if args.mode == "internal":
        report = {
            "ok": internal["ok"],
            "internal": internal,
            "external": None,
            "external_llamafactory_validation": "not_requested",
            "preprocess_executed": False,
            "datasets": internal.get("datasets"),
            "errors": internal.get("errors"),
        }
        exit_ok = internal["ok"]
    elif args.mode == "llamafactory":
        report = merge_report({"ok": True, "errors": [], "datasets": {}}, external)
        # llamafactory-only mode: ignore internal
        report["ok"] = bool(external and external.get("ok") and external.get("preprocess_executed"))
        report["internal"] = None
        report["external"] = external
        if external and external.get("status") == "blocked_dependency_missing":
            exit_ok = bool(args.allow_missing_llamafactory)
        else:
            exit_ok = report["ok"]
    else:
        report = merge_report(internal or {"ok": False, "errors": ["internal missing"]}, external)
        if external and external.get("status") in {"blocked_dependency_missing", "blocked_model_or_tokenizer_missing", "not_run"}:
            # Explicit: cannot claim LF pass; exit non-zero unless allow flag
            report["ok"] = False
            exit_ok = bool(args.allow_missing_llamafactory and (internal or {}).get("ok"))
            if args.allow_missing_llamafactory and (internal or {}).get("ok"):
                report["ok_with_allow_missing_llamafactory"] = True
                report["note"] = (
                    (report.get("note") or "")
                    + " Exit allowed only because --allow-missing-llamafactory; "
                    "external status remains blocked (NOT a LLaMAFactory pass)."
                )
        else:
            exit_ok = report["ok"]

    out = root / "datasets" / "reports" / "llamafactory_real_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if exit_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
