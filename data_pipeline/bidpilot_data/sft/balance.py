from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable

from bidpilot_data.settings import get_settings, load_yaml


def load_sft_balance_config() -> dict[str, Any]:
    settings = get_settings()
    path = settings.configs_root / "sft_balance.yaml"
    if path.exists():
        return load_yaml(path)
    return {}


def balance_records(
    records: list[Any],
    *,
    get_task: Callable[[Any], str],
    get_quality: Callable[[Any], str],
    get_review: Callable[[Any], str],
    get_confidence: Callable[[Any], float],
    has_complete_source: Callable[[Any], bool],
    is_test_split_record: Callable[[Any], bool] | None = None,
    protect_gold_in_test: bool = True,
) -> tuple[list[Any], dict[str, Any]]:
    """Downsample over-represented tasks only. Never clone or rewrite.

    Uncapped tasks are kept in full. Capped tasks (`max_ratio`) are sized so
    that each is at most ``max_ratio`` of the *final* kept total, using
    ``final ≈ uncapped / (1 - sum(max_ratios))`` then a short refine pass.
    """
    cfg = load_sft_balance_config().get("task_ratios") or {}
    before = Counter(get_task(r) for r in records)

    by_task: dict[str, list[Any]] = defaultdict(list)
    for r in records:
        by_task[get_task(r)].append(r)

    def sort_key(r: Any) -> tuple:
        q = get_quality(r)
        return (
            0 if q == "gold" else 1,
            -get_confidence(r),
            0 if has_complete_source(r) else 1,
            0 if get_review(r) == "reviewed" else 1,
        )

    def is_protected(r: Any) -> bool:
        return bool(
            protect_gold_in_test
            and is_test_split_record is not None
            and get_quality(r) == "gold"
            and is_test_split_record(r)
        )

    uncapped: dict[str, list[Any]] = {}
    capped: dict[str, tuple[float, list[Any]]] = {}
    for task, items in by_task.items():
        items_sorted = sorted(items, key=sort_key)
        rule = cfg.get(task) or {}
        max_ratio = rule.get("max_ratio")
        if max_ratio is None:
            uncapped[task] = items_sorted
        else:
            capped[task] = (float(max_ratio), items_sorted)

    u_n = sum(len(v) for v in uncapped.values())
    ratio_sum = sum(r for r, items in capped.values() if items)
    total = len(records) or 1
    degenerate = ratio_sum >= 0.95 or u_n == 0
    if degenerate:
        # No uncapped anchor: cap vs pre-balance total once (avoid iterative collapse)
        final_est = total
    else:
        final_est = max(u_n + 1, int(u_n / (1.0 - ratio_sum)))

    selected: dict[str, list[Any]] = {t: list(v) for t, v in uncapped.items()}
    dropped: dict[str, int] = {}

    def take_capped(max_ratio: float, items: list[Any], budget_total: int) -> list[Any]:
        protected = [r for r in items if is_protected(r)]
        others = [r for r in items if not is_protected(r)]
        max_n = max(len(protected), int(budget_total * max_ratio))
        return protected + others[: max(0, max_n - len(protected))]

    for task, (max_ratio, items) in capped.items():
        chosen = take_capped(max_ratio, items, final_est)
        if len(chosen) < len(items):
            dropped[task] = len(items) - len(chosen)
        selected[task] = chosen

    # Refine against actual kept total only when uncapped tasks anchor the total
    if not degenerate:
        for _ in range(8):
            kept_total = sum(len(v) for v in selected.values()) or 1
            changed = False
            for task, (max_ratio, _items) in capped.items():
                limit = max(1, int(kept_total * max_ratio))
                cur = selected.get(task) or []
                protected = [r for r in cur if is_protected(r)]
                if len(cur) <= max(len(protected), limit):
                    continue
                others = [r for r in cur if not is_protected(r)]
                new_items = protected + others[: max(0, limit - len(protected))]
                dropped[task] = dropped.get(task, 0) + (len(cur) - len(new_items))
                selected[task] = new_items
                changed = True
            if not changed:
                break

    kept: list[Any] = []
    for items in selected.values():
        kept.extend(items)

    after_cap = Counter(get_task(r) for r in kept)
    kept_total = len(kept) or 1
    task_gaps: dict[str, Any] = {}
    for task, rule in cfg.items():
        target = rule.get("target_ratio")
        if target is None:
            continue
        have = after_cap.get(task, 0)
        want = int(kept_total * float(target))
        if have < want:
            task_gaps[task] = {
                "have": have,
                "target_count": want,
                "target_ratio": target,
                "gap": want - have,
                "note": "insufficient real samples; no cloning allowed",
            }

    report = {
        "before_balance": dict(before),
        "after_balance": dict(after_cap),
        "dropped_by_balance": dropped,
        "task_gaps": task_gaps,
        "total_before": sum(before.values()),
        "total_after": sum(after_cap.values()),
        "max_ratio_basis": "final_kept_total_with_uncapped_anchor",
    }
    return kept, report
