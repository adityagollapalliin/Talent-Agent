"""Optional paid aggregator: SerpApi Google Jobs (off by default; `sources.aggregator.enabled`)."""

from __future__ import annotations

from skillpulse.models import RawPosting
from skillpulse.sources.base import BANNED_DOMAINS, FetchContext, SourceSkipped
from skillpulse.sources.detect import detect_employment_type, detect_work_mode, map_employment
from skillpulse.util.retry import RateLimiter, request_json

SERPAPI = "https://serpapi.com/search.json"


def parse_serpapi(payload: dict, search_title: str) -> list[RawPosting]:
    out = []
    for j in payload.get("jobs_results", []):
        # Prefer a direct employer/ATS apply link over aggregator pages we must not scrape.
        links = [o.get("link") for o in j.get("apply_options") or [] if o.get("link")]
        links = [u for u in links if not any(d in u.lower() for d in BANNED_DOMAINS)]
        if not links:
            continue  # only aggregator/banned links (or a Google share link): not a real posting URL
        url = links[0]
        ext = j.get("detected_extensions") or {}
        desc = j.get("description", "")
        title, loc = j.get("title", ""), j.get("location", "")
        emp = map_employment(ext.get("schedule_type"))
        out.append(RawPosting(
            source="serpapi", url=url, title=title, company=j.get("company_name", ""), location=loc,
            work_mode="remote" if ext.get("work_from_home") else detect_work_mode(title, loc, desc),
            employment_type=emp if emp != "unknown" else detect_employment_type(title, desc),
            posted_date=None, description_text=desc, search_title=search_title))
    return out


class SerpApiSource:
    name = "aggregator"
    assume_india = False

    def fetch(self, ctx: FetchContext) -> list[RawPosting]:
        cfg = ctx.settings.source("aggregator")
        if not cfg.enabled:
            raise SourceSkipped("disabled in config (paid, optional)")
        if cfg.opt("provider", "serpapi") != "serpapi":
            raise SourceSkipped(f"provider {cfg.opt('provider')!r} not implemented (only serpapi)")
        if not ctx.secrets.serpapi_key:
            raise SourceSkipped("SERPAPI_API_KEY not set")
        limiter = RateLimiter(cfg.min_interval_s)
        region = ctx.settings.region
        out: list[RawPosting] = []
        for _fam, title in ctx.all_titles():
            key = f"{title}|{region.preferred_city}"
            if ctx.store.search_done_recently(self.name, key, ctx.settings.cache.search_repeat_hours):
                continue
            payload = request_json(ctx.http, "GET", SERPAPI, limiter=limiter, params={
                "engine": "google_jobs", "q": title, "location": f"{region.preferred_city}, {region.country}",
                "gl": region.country_code, "hl": "en", "api_key": ctx.secrets.serpapi_key})
            got = parse_serpapi(payload, title)[: ctx.settings.max_postings_per_title_per_run]
            ctx.store.record_search(self.name, key, len(got))
            out.extend(got)
        return out
