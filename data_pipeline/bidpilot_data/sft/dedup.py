from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from rapidfuzz import fuzz


def normalize_user_text(text: str) -> str:
    """Unicode normalize, collapse whitespace, unify digits/dates; keep business entities."""
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("\u3000", " ")
    # Unify date-like forms
    t = re.sub(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", r"\1-\2-\3", t)
    t = re.sub(r"(20\d{2})[./](\d{1,2})[./](\d{1,2})", r"\1-\2-\3", t)
    # Unify digit separators in amounts (keep digits)
    t = re.sub(r"(\d),(\d)", r"\1\2", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _tokenize(text: str) -> list[str]:
    # CJK bigrams + latin/digit tokens
    chars = re.findall(r"[\u4e00-\u9fff]|[a-z0-9]+", text.lower())
    grams: list[str] = []
    buf = ""
    for ch in chars:
        if len(ch) == 1 and "\u4e00" <= ch <= "\u9fff":
            buf += ch
        else:
            if len(buf) >= 2:
                grams.extend(buf[i : i + 2] for i in range(len(buf) - 1))
            elif buf:
                grams.append(buf)
            buf = ""
            grams.append(ch)
    if len(buf) >= 2:
        grams.extend(buf[i : i + 2] for i in range(len(buf) - 1))
    elif buf:
        grams.append(buf)
    return grams or [text[:8] or "empty"]


def simhash64(text: str) -> int:
    weights = [0] * 64
    for tok in _tokenize(text):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        for i in range(64):
            weights[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i, w in enumerate(weights):
        if w >= 0:
            out |= 1 << i
    return out


def hamming64(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass
class DedupStats:
    exact_duplicates_removed: int = 0
    near_duplicates_removed: int = 0
    cross_project_template_duplicates: int = 0
    conflicting_gold_records: list[dict[str, Any]] = field(default_factory=list)


def global_near_dedup(
    records: list[Any],
    *,
    get_task: Callable[[Any], str],
    get_user: Callable[[Any], str],
    get_quality: Callable[[Any], str],
    get_project: Callable[[Any], str],
    get_id: Callable[[Any], str],
    near_threshold: int = 95,
    simhash_hamming_max: int = 3,
    cross_project_template_check: bool = True,
) -> tuple[list[Any], DedupStats]:
    """Global approximate dedup by task_type using exact hash + SimHash LSH + fuzzy confirm.

    Gold preferred over silver. Two gold conflicts are retained and logged for review.
    """
    stats = DedupStats()
    by_task: dict[str, list[Any]] = defaultdict(list)
    for r in records:
        by_task[get_task(r)].append(r)

    kept_all: list[Any] = []
    for task, group in by_task.items():
        # Prefer gold first so it wins exact/near collisions
        group = sorted(group, key=lambda r: (0 if get_quality(r) == "gold" else 1, get_id(r)))
        exact: dict[str, Any] = {}
        for r in group:
            key = hashlib.sha1(normalize_user_text(get_user(r)).encode("utf-8")).hexdigest()
            if key in exact:
                stats.exact_duplicates_removed += 1
                prev = exact[key]
                if get_quality(r) == "gold" and get_quality(prev) == "gold" and get_id(r) != get_id(prev):
                    stats.conflicting_gold_records.append(
                        {
                            "task_type": task,
                            "record_ids": [get_id(prev), get_id(r)],
                            "project_ids": [get_project(prev), get_project(r)],
                            "reason": "exact_user_text",
                        }
                    )
                    # Keep both golds for human review (do not drop second gold)
                    kept_all.append(r)
                continue
            exact[key] = r

        candidates = list(exact.values())
        # LSH bands: 4 bands of 16 bits
        buckets: dict[tuple[int, int], list[tuple[int, Any]]] = defaultdict(list)
        kept: list[Any] = []
        kept_hashes: list[tuple[int, str, str, Any]] = []  # simhash, norm, project, record

        for r in candidates:
            user = get_user(r)
            norm = normalize_user_text(user)
            sh = simhash64(norm)
            is_dup = False
            # Probe neighboring band buckets
            for band in range(4):
                band_key = (band, (sh >> (band * 16)) & 0xFFFF)
                for prev_sh, prev in buckets.get(band_key, []):
                    if hamming64(sh, prev_sh) > simhash_hamming_max:
                        continue
                    prev_norm = normalize_user_text(get_user(prev))
                    score = fuzz.token_set_ratio(norm, prev_norm)
                    if score < near_threshold:
                        continue
                    # Similar content
                    same_project = get_project(r) == get_project(prev)
                    if not same_project and cross_project_template_check:
                        # Protect distinct projects if only shared template prefix is short
                        # Drop only when high similarity AND substantial shared body after stripping template shell
                        body_r = re.sub(r"^(判断以下条款|抽取资格要求|识别风险|抽取评分条目)[：:]\s*", "", norm)
                        body_p = re.sub(r"^(判断以下条款|抽取资格要求|识别风险|抽取评分条目)[：:]\s*", "", prev_norm)
                        if fuzz.token_set_ratio(body_r, body_p) < near_threshold:
                            continue
                        stats.cross_project_template_duplicates += 1
                    q_r, q_p = get_quality(r), get_quality(prev)
                    if q_r == "gold" and q_p == "gold":
                        stats.conflicting_gold_records.append(
                            {
                                "task_type": task,
                                "record_ids": [get_id(prev), get_id(r)],
                                "project_ids": [get_project(prev), get_project(r)],
                                "reason": "near_duplicate_gold",
                                "score": score,
                            }
                        )
                        # Keep both golds
                        is_dup = False
                        break
                    if q_r == "gold" and q_p != "gold":
                        # Replace previous silver with gold
                        if prev in kept:
                            kept.remove(prev)
                            stats.near_duplicates_removed += 1
                        is_dup = False
                        break
                    # Drop current (silver vs gold or silver vs silver)
                    stats.near_duplicates_removed += 1
                    is_dup = True
                    break
                if is_dup:
                    break
            if is_dup:
                continue
            kept.append(r)
            for band in range(4):
                buckets[(band, (sh >> (band * 16)) & 0xFFFF)].append((sh, r))
            kept_hashes.append((sh, norm, get_project(r), r))

        kept_all.extend(kept)

    return kept_all, stats
