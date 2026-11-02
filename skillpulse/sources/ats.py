"""Public ATS job-board APIs: Greenhouse, Lever, Ashby. Companies come from config/companies.yaml.

These endpoints are the companies' own public, unauthenticated job-board APIs, published for embedding job lists.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime, UTC
from typing import Any

from skillpulse.config import load_companies
from skillpulse.models import RawPosting
from skillpulse.sources.base import FetchContext, SourceSkipped
from skillpulse.sources.detect import detect_employment_type, detect_work_mode, map_employment
from skillpulse.sources.matching import LocationFilter, TitleMatcher
from skillpulse.util.retry import RateLimiter, request_json
from skillpulse.util.text import html_to_text, normalise_ws

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER = "https://api.lever.co/v0/postings/{token}"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def _iso_date(v: Any) -> str | None:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):  # Lever: epoch ms
            return datetime.fromtimestamp(v / 1000, tz=UTC).date().isoformat()
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date().isoformat()
    except (ValueError, OSError):
        return None


def _mode(value: str | None) -> str:
    v = (value or "").lower().replace("-", "").replace(" ", "")
    return {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "inoffice": "onsite"}.get(v, "unknown")


def parse_greenhouse(payload: dict, company: str) -> list[RawPosting]:
    out = []
    for j in payload.get("jobs", []):
        title, url = normalise_ws(j.get("title", "")), j.get("absolute_url")
        if not title or not url:
            continue
        desc = html_to_text(j.get("content", ""))
        loc = (j.get("location") or {}).get("name", "") or ""
        out.append(RawPosting(
            source="greenhouse", url=url, title=title, company=company, location=loc,
            work_mode=detect_work_mode(title, loc, desc), employment_type=detect_employment_type(title, desc),
            posted_date=_iso_date(j.get("first_published") or j.get("updated_at")), description_text=desc))
    return out


def parse_lever(payload: list, company: str) -> list[RawPosting]:
    out = []
    for j in payload or []:
        title, url = normalise_ws(j.get("text", "")), j.get("hostedUrl")
        if not title or not url:
            continue
        cats = j.get("categories") or {}
        lists = "\n".join(f"{x.get('text', '')}:\n{html_to_text(x.get('content', ''))}" for x in j.get("lists") or [])
        desc = "\n".join(filter(None, [j.get("descriptionPlain", ""), lists, j.get("additionalPlain", "")]))
        loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        mode = _mode(j.get("workplaceType"))
        emp = map_employment(cats.get("commitment"))
        out.append(RawPosting(
            source="lever", url=url, title=title, company=company, location=loc,
            work_mode=mode if mode != "unknown" else detect_work_mode(title, loc, desc),
            employment_type=emp if emp != "unknown" else detect_employment_type(title, desc),
            posted_date=_iso_date(j.get("createdAt")), description_text=desc.strip()))
    return out


def parse_ashby(payload: dict, company: str) -> list[RawPosting]:
    out = []
    for j in payload.get("jobs", []):
        if j.get("isListed") is False:
            continue
        title, url = normalise_ws(j.get("title", "")), j.get("jobUrl")
        if not title or not url:
            continue
        locs = [j.get("location") or ""] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
        loc = "; ".join(x for x in locs if x)
        desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
        mode = _mode(j.get("workplaceType")) if j.get("workplaceType") else ("remote" if j.get("isRemote") else "unknown")
        emp = map_employment(j.get("employmentType"))
        out.append(RawPosting(
            source="ashby", url=url, title=title, company=company, location=loc,
            work_mode=mode if mode != "unknown" else detect_work_mode(title, loc, desc),
            employment_type=emp if emp != "unknown" else detect_employment_type(title, desc),
            posted_date=_iso_date(j.get("publishedAt")), description_text=desc))
    return out


BOARDS: dict[str, tuple[str, dict[str, Any], Callable[[Any, str], list[RawPosting]]]] = {
    "greenhouse": (GREENHOUSE, {"content": "true"}, parse_greenhouse),
    "lever": (LEVER, {"mode": "json"}, parse_lever),
    "ashby": (ASHBY, {"includeCompensation": "false"}, parse_ashby),
}


class AtsBoardsSource:
    name = "ats_boards"

    def __init__(self) -> None:
        self.last_warnings: list[str] = []  # per-board failures, shown in run health

    def fetch(self, ctx: FetchContext) -> list[RawPosting]:
        cfg = ctx.settings.source("ats_boards")
        if not cfg.enabled:
            raise SourceSkipped("disabled in config")
        companies = load_companies(ctx.paths.root / cfg.opt("companies_file", "config/companies.yaml"))
        if not any(companies.get(k) for k in BOARDS):
            raise SourceSkipped("no companies configured")
        limiter = RateLimiter(cfg.min_interval_s)
        matcher, locf = TitleMatcher.from_settings(ctx.settings), LocationFilter(ctx.settings)
        per_title: Counter[str] = Counter()
        cap = ctx.settings.max_postings_per_title_per_run
        out: list[RawPosting] = []
        errors: list[str] = []
        for kind, (url_t, params, parser) in BOARDS.items():
            for co in companies.get(kind, []):
                token, name = co["token"], co.get("name", co["token"])
                key = f"{kind}|{token}"
                if ctx.store.search_done_recently(self.name, key, ctx.settings.cache.search_repeat_hours):
                    continue
                try:
                    payload = request_json(ctx.http, "GET", url_t.format(token=token), params=params, limiter=limiter)
                    jobs = parser(payload, name)
                except Exception as e:  # noqa: BLE001 — one bad board token must not stop the others
                    errors.append(f"{kind}:{token}: {type(e).__name__}")
                    ctx.log.warning("ats board failed", extra={"board": key, "error": str(e)[:200]})
                    continue
                kept = 0
                for p in jobs:
                    m = matcher.match(p.title)
                    if not m or not locf.in_india(p):
                        continue  # cheap pre-filter; the pipeline re-checks everything
                    if per_title[m[1]] >= cap:
                        continue
                    per_title[m[1]] += 1
                    p.search_title = m[1]
                    out.append(p)
                    kept += 1
                ctx.store.record_search(self.name, key, kept)
        self.last_warnings = errors
        if errors and not out:
            raise RuntimeError("all ATS boards failed: " + "; ".join(errors[:10]))
        if errors:
            ctx.log.warning("some ATS boards failed", extra={"failed": errors})
        return out
