"""The daily run: fetch → normalise/filter → dedupe → classify → (extract → aggregate → report → email)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from skillpulse.classify.classifier import LLMClassifier, classify
from skillpulse.classify.rubric import Rubric
from skillpulse.config import Settings
from skillpulse.models import RawPosting, SourceStatus
from skillpulse.sources.base import BANNED_DOMAINS, FetchContext, Source, SourceSkipped
from skillpulse.sources.detect import detect_employment_type, detect_work_mode
from skillpulse.sources.matching import LocationFilter, TitleMatcher
from skillpulse.store.base import Store

if TYPE_CHECKING:
    from skillpulse.extract.aliases import Aliases
    from skillpulse.llm.base import LLMClient

log = logging.getLogger("skillpulse.pipeline")


@dataclass
class CollectStats:
    fetched: int = 0
    off_target_title: int = 0
    outside_region: int = 0
    banned_domain: int = 0
    new: int = 0
    cached: int = 0
    new_ids: list[int] = field(default_factory=list)
    statuses: list[SourceStatus] = field(default_factory=list)


def normalise_posting(p: RawPosting, matcher: TitleMatcher, locf: LocationFilter, assume_india: bool) -> str | None:
    """Fill in family/city/mode/type in place. Returns a drop reason, or None to keep."""
    if any(d in p.url.lower() for d in BANNED_DOMAINS):
        return "banned_domain"
    m = matcher.match(p.title)
    if m is None:
        return "off_target_title"
    p.role_family, matched = m
    p.search_title = p.search_title or matched
    if p.work_mode == "unknown":
        p.work_mode = detect_work_mode(p.title, p.location, p.description_text)
    if p.employment_type == "unknown":
        p.employment_type = detect_employment_type(p.title, p.description_text)
    p.city = p.city or locf.city(p.location)
    if not locf.in_india(p, assume_india=assume_india):
        return "outside_region"
    if not locf.keep(p):
        return "outside_region"
    return None


def collect(sources: list[Source], ctx: FetchContext, today: str) -> CollectStats:
    """Run every source (a failing source is logged and skipped), normalise, filter, upsert."""
    st = CollectStats()
    matcher = TitleMatcher.from_settings(ctx.settings)
    locf = LocationFilter(ctx.settings)
    for src in sources:
        status = SourceStatus(name=src.name, ok=True)
        try:
            ctx.already_fetched = st.new + st.cached
            raw = src.fetch(ctx)
        except SourceSkipped as e:
            status.skipped_reason = str(e)
            st.statuses.append(status)
            log.info("source skipped", extra={"source": src.name, "reason": str(e)})
            continue
        except Exception as e:  # noqa: BLE001 — a broken source must not stop the run
            status.ok, status.error = False, f"{type(e).__name__}: {e}"[:500]
            st.statuses.append(status)
            log.exception("source failed", extra={"source": src.name})
            continue
        status.fetched = len(raw)
        st.fetched += len(raw)
        for p in raw:
            reason = normalise_posting(p, matcher, locf, assume_india=getattr(src, "assume_india", False))
            if reason:
                setattr(st, reason, getattr(st, reason) + 1)
                continue
            pid, is_new = ctx.store.upsert_posting(p, today)
            if is_new:
                st.new += 1
                st.new_ids.append(pid)
            else:
                st.cached += 1
        st.statuses.append(status)
        log.info("source done", extra={"source": src.name, "fetched": len(raw)})
    return st


def classify_pending(store: Store, settings: Settings, rubric: Rubric, llm: LLMClassifier | None) -> dict[str, int]:
    """Classify every unclassified posting; mark ones outside report_levels / employment_types as skipped."""
    counts: dict[str, int] = {}
    for row in store.postings_needing_level():
        res = classify(row["title"], row["description_text"] or "", rubric, row["employment_type"], llm)
        if res is None:
            counts["unclassified"] = counts.get("unclassified", 0) + 1
            continue
        store.set_level(row["id"], res)
        counts[res.level] = counts.get(res.level, 0) + 1
        if res.level not in settings.report_levels:
            store.set_status(row["id"], "skipped_level")
        elif row["employment_type"] not in (*settings.employment_types, "unknown"):
            store.set_status(row["id"], "skipped_employment")
    return counts


# ----------------------------------------------------------------------------------------- reporting
def build_report(day: str, store: Store, settings: Settings, aliases: "Aliases", llm: "LLMClient | None",
                 run_health: dict) -> dict:
    """Aggregate today's snapshot, persist daily aggregates, add prose. Returns the report data dict."""
    from skillpulse.report.aggregate import aggregate_rows, build_buckets, build_report_data
    from skillpulse.report.prose import write_prose

    snapshot = store.snapshot(day, settings.cache.ttl_days)
    store.save_daily_aggregates(day, aggregate_rows(build_buckets(snapshot, settings), aliases))
    data = build_report_data(day, snapshot, store, settings, aliases, run_health)
    return write_prose(data, llm)


def write_report_files(data: dict, reports_dir: Path, docx: bool = True) -> dict[str, Path]:
    import json

    from skillpulse.report.markdown import render_markdown

    reports_dir.mkdir(parents=True, exist_ok=True)
    day = data["date"]
    out = {"md": reports_dir / f"{day}.md", "json": reports_dir / f"{day}.json"}
    out["md"].write_text(render_markdown(data), encoding="utf-8")
    out["json"].write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    if docx:
        from skillpulse.report.docx_builder import build_docx

        out["docx"] = build_docx(data, reports_dir / f"{day}.docx")
    return out


def write_memory(day: str, data: dict, store: Store, settings: Settings, memory_dir: Path) -> Path:
    from skillpulse.report.memory_md import render_memory

    path = memory_dir / "MEMORY.md"
    path.write_text(render_memory(day, data, store.stats(), store.snapshot(day, settings.cache.ttl_days),
                                  store.last_successful_run()), encoding="utf-8")
    return path
