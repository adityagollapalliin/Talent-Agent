"""Adzuna API (https://developer.adzuna.com). Country `in`; Hyderabad + India-wide remote."""

from __future__ import annotations

from datetime import datetime

from skillpulse.models import RawPosting
from skillpulse.sources.base import FetchContext, SourceSkipped
from skillpulse.sources.detect import detect_employment_type, detect_work_mode
from skillpulse.util.retry import RateLimiter, request_json
from skillpulse.util.text import normalise_ws

BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


def parse_results(payload: dict, search_title: str, family: str) -> list[RawPosting]:
    """Adzuna search JSON → RawPosting list (pure; tested on recorded fixtures)."""
    out: list[RawPosting] = []
    for r in payload.get("results", []):
        url = r.get("redirect_url")
        title = normalise_ws(r.get("title", ""))
        if not url or not title:
            continue
        desc = normalise_ws(r.get("description", ""))
        location = (r.get("location") or {}).get("display_name", "") or ""
        ct, ctype = r.get("contract_time"), r.get("contract_type")
        if ct == "full_time" or ctype == "permanent":
            emp = "full-time"
        elif ctype == "contract":
            emp = "contract"
        elif ct == "part_time":
            emp = "part-time"
        else:
            emp = detect_employment_type(title, desc)
        if detect_employment_type(title) == "internship":
            emp = "internship"
        posted = r.get("created")
        try:
            posted = datetime.fromisoformat(posted.replace("Z", "+00:00")).date().isoformat() if posted else None
        except ValueError:
            posted = None
        out.append(RawPosting(
            source="adzuna", url=url, title=title,
            company=((r.get("company") or {}).get("display_name") or "").strip(),
            location=location, work_mode=detect_work_mode(title, location, desc),
            employment_type=emp, posted_date=posted, description_text=desc,
            search_title=search_title, role_family=family,
        ))
    return out


class AdzunaSource:
    name = "adzuna"
    assume_india = True  # country code "in" is in the URL

    def fetch(self, ctx: FetchContext) -> list[RawPosting]:
        cfg = ctx.settings.source("adzuna")
        if not cfg.enabled:
            raise SourceSkipped("disabled in config")
        if not (ctx.secrets.adzuna_app_id and ctx.secrets.adzuna_app_key):
            raise SourceSkipped("ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
        limiter = RateLimiter(cfg.min_interval_s)
        per_title = ctx.settings.max_postings_per_title_per_run
        country = ctx.settings.region.country_code
        url = BASE.format(country=country, page=1)
        out: list[RawPosting] = []
        queries = []
        for fam, title in ctx.all_titles():
            queries.append((fam, title, {"what_phrase": title, "where": ctx.settings.region.preferred_city}))
            if ctx.settings.region.include_india_remote:
                queries.append((fam, title, {"what_phrase": title, "what_or": "remote hybrid"}))
        for fam, title, q in queries:
            key = f"{country}|{sorted(q.items())}"
            if ctx.store.search_done_recently(self.name, key, ctx.settings.cache.search_repeat_hours):
                continue
            params = {
                "app_id": ctx.secrets.adzuna_app_id, "app_key": ctx.secrets.adzuna_app_key,
                "results_per_page": min(per_title, int(cfg.opt("results_per_page", 50))),
                "max_days_old": int(cfg.opt("max_days_old", 30)), "sort_by": "date",
                "content-type": "application/json", **q,
            }
            payload = request_json(ctx.http, "GET", url, params=params, limiter=limiter)
            got = parse_results(payload, title, fam)
            ctx.store.record_search(self.name, key, len(got))
            out.extend(got)
        return out
