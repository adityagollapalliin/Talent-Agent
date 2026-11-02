"""All report numbers are computed here, in Python. The LLM never sees raw JDs at this stage and never computes figures.

Buckets are (role_family, level) for level in report_levels, plus ("__all__", "L1+L2") for market-wide risers.
Shares are % of *extracted* postings in the bucket's TTL snapshot that mention the item.
Baselines: mean share over stored daily aggregates in the previous 7 / 30 days (days where the bucket had data;
an item absent on such a day counts as 0%).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from skillpulse.config import Settings
from skillpulse.extract.aliases import Aliases
from skillpulse.store.base import Store

ALL = "__all__"
N_SENTINEL = ("_n", "_n")  # (category, item) row that records bucket size, so absent items count as 0%
CATEGORIES = ("skill", "must", "nice", "cert", "expectation", "focus_any", "focus_must", "focus_nice")


def family_label(key: str, settings: Settings) -> str:
    rf = settings.role_families.get(key)
    if rf is not None and rf.label:
        return rf.label
    return key.replace("_", " ").title()


@dataclass
class Bucket:
    family: str
    level: str
    rows: list[dict[str, Any]]  # snapshot postings with an extraction

    @property
    def n(self) -> int:
        return len(self.rows)


def bucket_counts(rows: list[dict[str, Any]], aliases: Aliases) -> dict[str, Counter]:
    """Per-category posting counts (each posting counts an item at most once)."""
    c: dict[str, Counter] = {k: Counter() for k in CATEGORIES}
    for r in rows:
        e = r["extraction"]
        must, nice = set(e.get("must_have_skills", [])), set(e.get("nice_to_have_skills", []))
        c["must"].update(must)
        c["nice"].update(nice - must)
        c["skill"].update(must | nice)
        c["cert"].update(set(e.get("certifications", [])))
        c["expectation"].update(set(e.get("soft_skills_and_expectations", [])))
        c["focus_any"].update(set(e.get("matches_focus_technologies", [])))
        fm = set(aliases.focus_hits(list(must)))
        c["focus_must"].update(fm)
        c["focus_nice"].update(set(aliases.focus_hits(list(nice))) - fm)
    return c


def build_buckets(snapshot: list[dict[str, Any]], settings: Settings) -> list[Bucket]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in snapshot:
        if r.get("extraction") is None or r.get("level") not in settings.report_levels:
            continue
        by[(r["role_family"], r["level"])].append(r)
        if r["level"] in settings.primary_levels:
            by[(ALL, "+".join(settings.primary_levels))].append(r)
    out = []
    for fam in [*settings.role_families.keys(), ALL]:
        for lvl in [*settings.report_levels, "+".join(settings.primary_levels)]:
            if (fam, lvl) in by:
                out.append(Bucket(fam, lvl, by[(fam, lvl)]))
    return out


def aggregate_rows(buckets: list[Bucket], aliases: Aliases) -> list[dict[str, Any]]:
    """Rows for store.save_daily_aggregates()."""
    rows: list[dict[str, Any]] = []
    for b in buckets:
        rows.append({"family": b.family, "level": b.level, "category": N_SENTINEL[0], "item": N_SENTINEL[1],
                     "count": b.n, "n_postings": b.n, "share": 100.0})
        for cat, ctr in bucket_counts(b.rows, aliases).items():
            for item, cnt in ctr.items():
                rows.append({"family": b.family, "level": b.level, "category": cat, "item": item,
                             "count": cnt, "n_postings": b.n, "share": round(100.0 * cnt / b.n, 2)})
    return rows


class Baselines:
    """Mean share per (family, level, category, item) over a trailing window, from stored aggregates."""

    def __init__(self, store: Store, day: str, window_days: int):
        d = date.fromisoformat(day)
        lo, hi = (d - timedelta(days=window_days)).isoformat(), (d - timedelta(days=1)).isoformat()
        rows = store.get_aggregates(lo, hi)
        self.days: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.sums: dict[tuple[str, str, str, str], float] = defaultdict(float)
        for r in rows:
            if (r["category"], r["item"]) == N_SENTINEL:
                self.days[(r["family"], r["level"])].add(r["day"])
            else:
                self.sums[(r["family"], r["level"], r["category"], r["item"])] += r["share"]

    def has(self, family: str, level: str) -> bool:
        return bool(self.days.get((family, level)))

    def share(self, family: str, level: str, category: str, item: str) -> float | None:
        n = len(self.days.get((family, level), ()))
        if not n:
            return None
        return self.sums.get((family, level, category, item), 0.0) / n


def arrow(delta: float | None, eps: float) -> str:
    if delta is None:
        return "–"
    if delta > eps:
        return "↑"
    if delta < -eps:
        return "↓"
    return "="


def _delta(today: float, base: float | None) -> float | None:
    return None if base is None else round(today - base, 1)


def ranked(ctr: Counter, n: int, top: int) -> list[tuple[str, int, float]]:
    items = sorted(ctr.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:top]
    return [(k, v, round(100.0 * v / n, 1)) for k, v in items]


def build_report_data(day: str, snapshot: list[dict[str, Any]], store: Store, settings: Settings,
                      aliases: Aliases, run_health: dict[str, Any]) -> dict[str, Any]:
    rc = settings.report
    eps = rc.trend_epsilon_pp
    buckets = build_buckets(snapshot, settings)
    b7, b30 = Baselines(store, day, 7), Baselines(store, day, 30)
    pref = settings.region.preferred_city
    primary_key = "+".join(settings.primary_levels)

    def skill_table(b: Bucket, ctr: Counter, top: int, category: str = "skill") -> list[dict[str, Any]]:
        out = []
        for item, cnt, share in ranked(ctr, b.n, top):
            d7 = _delta(share, b7.share(b.family, b.level, category, item))
            d30 = _delta(share, b30.share(b.family, b.level, category, item))
            out.append({"skill": item, "count": cnt, "share": share, "d7": d7, "d30": d30,
                        "arrow7": arrow(d7, eps), "arrow30": arrow(d30, eps)})
        return out

    def rising(b: Bucket, ctr: Counter, limit: int) -> list[dict[str, Any]]:
        if not b7.has(b.family, b.level):
            return []
        out = []
        for item, cnt in ctr.items():
            share = round(100.0 * cnt / b.n, 1)
            base = b7.share(b.family, b.level, "skill", item) or 0.0
            if share - base > rc.rising_threshold_pp:
                out.append({"skill": item, "share": share, "d7": round(share - base, 1), "new": base == 0.0})
        out.sort(key=lambda x: (-x["d7"], x["skill"]))
        return out[:limit]

    def focus_table(b: Bucket, c: dict[str, Counter]) -> list[dict[str, Any]]:
        rows = []
        for group, techs in settings.focus_technologies.items():
            for t in techs:
                rows.append({"group": group, "tech": t,
                             "must": round(100.0 * c["focus_must"][t] / b.n, 1),
                             "nice": round(100.0 * c["focus_nice"][t] / b.n, 1),
                             "any": round(100.0 * c["focus_any"][t] / b.n, 1)})
        return rows

    def samples(b: Bucket) -> list[dict[str, Any]]:
        rows = sorted(b.rows, key=lambda r: (r.get("first_seen") or "", r.get("id") or 0), reverse=True)
        rows.sort(key=lambda r: r.get("city") != pref)  # stable: Hyderabad first, newest first within each
        return [{"title": r["title"], "company": r.get("company"), "city": r.get("city") or "",
                 "work_mode": r.get("work_mode"), "url": r["url"]} for r in rows[: rc.sample_links]]

    families_out = []
    bucket_map = {(b.family, b.level): b for b in buckets}
    for fam in settings.role_families:
        levels_out = []
        for lvl in settings.report_levels:
            b = bucket_map.get((fam, lvl))
            if b is None:
                continue
            c = bucket_counts(b.rows, aliases)
            is_primary = lvl in settings.primary_levels
            entry: dict[str, Any] = {
                "level": lvl, "primary": is_primary, "n": b.n,
                "top_skills": skill_table(b, c["skill"], rc.top_skills if is_primary else rc.top_skills_secondary),
                "certifications": [{"name": k, "count": v, "share": s} for k, v, s in ranked(c["cert"], b.n, 10)],
                "focus_table": focus_table(b, c),
            }
            if is_primary:
                entry["rising"] = rising(b, c["skill"], 10)
                entry["expectations"] = [{"trait": k, "count": v, "share": s} for k, v, s in ranked(c["expectation"], b.n, 8)]
                entry["ideal_candidate"] = ""  # filled by prose.py
                entry["samples"] = samples(b)
            levels_out.append(entry)
        families_out.append({"family": fam, "label": family_label(fam, settings), "levels": levels_out})

    # --- Today at a glance --------------------------------------------------------------------
    kept = snapshot
    hyd = [r for r in kept if r.get("city") == pref]
    others = [r for r in kept if r.get("city") != pref]
    level_counts = Counter(r.get("level") or "unclassified" for r in kept)
    all_b = bucket_map.get((ALL, primary_key))
    top_rising: list[dict[str, Any]] = []
    if all_b is not None:
        top_rising = rising(all_b, bucket_counts(all_b.rows, aliases)["skill"], 5)
    glance = {
        "total_postings": len(kept),
        "analysed": sum(1 for r in kept if r.get("extraction") is not None),
        "new_today": sum(1 for r in kept if r.get("first_seen") == day),
        "cached_today": sum(1 for r in kept if r.get("last_seen") == day and r.get("first_seen") != day),
        "split": {
            "preferred_city": pref,
            "preferred_total": len(hyd),
            "preferred_hybrid": sum(1 for r in hyd if r.get("work_mode") == "hybrid"),
            "preferred_remote": sum(1 for r in hyd if r.get("work_mode") == "remote"),
            "india_remote": sum(1 for r in others if r.get("work_mode") == "remote"),
            "hybrid_elsewhere": sum(1 for r in others if r.get("work_mode") == "hybrid"),
        },
        "levels": {k: level_counts.get(k, 0) for k in ["L0", "L1", "L2", "L3", "L4+", "unclassified"]},
        "top_rising": top_rising,
        "baseline_available": bool(all_b is not None and b7.has(ALL, primary_key)),
        "ttl_days": settings.cache.ttl_days,
    }

    # --- Certifications across the market -------------------------------------------------------
    cert_by_level: dict[str, Counter] = defaultdict(Counter)
    for r in kept:
        if r.get("extraction") and r.get("level") in settings.report_levels:
            for cert in set(r["extraction"].get("certifications", [])):
                cert_by_level[cert][r["level"]] += 1
    certs_market = sorted(
        ({"name": k, "total": sum(v.values()), "by_level": {lv: v.get(lv, 0) for lv in settings.report_levels}}
         for k, v in cert_by_level.items()),
        key=lambda x: (-x["total"], x["name"]),
    )[:15]

    return {
        "date": day,
        "glance": glance,
        "families": families_out,
        "certifications_market": certs_market,
        "health": run_health,
        "config": {"primary_levels": settings.primary_levels, "report_levels": settings.report_levels,
                   "rising_threshold_pp": rc.rising_threshold_pp},
    }
