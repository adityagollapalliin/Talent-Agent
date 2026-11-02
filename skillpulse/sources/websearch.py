"""LLM web search as a discovery source, only when API sources are thin.

Only results whose URL was actually returned by the provider's search tool are kept (no hallucinated links),
and banned job-board domains and search/listing pages are dropped. URLs are never fetched (no scraping).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from skillpulse.llm.base import BudgetExceeded
from skillpulse.models import RawPosting
from skillpulse.sources.base import BANNED_DOMAINS, FetchContext, SourceSkipped
from skillpulse.sources.detect import detect_employment_type, map_employment
from skillpulse.util.text import normalise_url

_LISTING = re.compile(r"/(search|jobs/?$|careers/?$|job-search|results)|[?&](q|query|keywords?)=", re.I)


class WebPosting(BaseModel):
    url: str
    title: str
    company: str
    location: str
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"]
    employment_type: str
    posted_date: str | None
    description: str


class WebSearchPostings(BaseModel):
    postings: list[WebPosting]


INSTRUCTIONS = (
    "You find CURRENT job postings using web search. Only return individual job postings (one job per URL) on company "
    "career sites or ATS boards (greenhouse.io, lever.co, ashbyhq.com, workday, smartrecruiters, etc.). Never return "
    "LinkedIn, Naukri, Indeed, Glassdoor, Instahyre or Wellfound links, search-result pages, or listing pages. "
    "Every URL must come from your search results. description: 3-8 sentences summarising the stated requirements "
    "(skills, years of experience, certifications, work mode). posted_date: YYYY-MM-DD or null."
)


def acceptable(url: str, returned: set[str]) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    low = url.lower()
    if any(d in low for d in BANNED_DOMAINS) or _LISTING.search(low):
        return False
    norm = normalise_url(url)
    return any(normalise_url(u) == norm for u in returned)


class WebSearchSource:
    name = "web_search"

    def fetch(self, ctx: FetchContext) -> list[RawPosting]:
        cfg = ctx.settings.source("web_search")
        if not cfg.enabled:
            raise SourceSkipped("disabled in config")
        threshold = int(cfg.opt("only_if_fewer_than", 40))
        if ctx.already_fetched >= threshold:
            raise SourceSkipped(f"API sources returned {ctx.already_fetched} ≥ {threshold}; not needed")
        if ctx.llm is None:
            raise SourceSkipped("no LLM client")
        region = ctx.settings.region
        out: list[RawPosting] = []
        max_q = int(cfg.opt("max_queries_per_run", 10))
        queries = []
        for fam, rf in ctx.settings.role_families.items():
            titles = ", ".join(rf.titles[:5])
            queries.append((fam, f"Job openings posted in the last 14 days in {region.preferred_city}, {region.country}, "
                                 f"or remote within {region.country}, for: {titles}. Full-time roles only."))
        for fam, q in queries[:max_q]:
            key = f"{fam}|{q}"
            if ctx.store.search_done_recently(self.name, key, ctx.settings.cache.search_repeat_hours):
                continue
            try:
                ctx.llm.guard("summary", len(q) + len(INSTRUCTIONS) + 20000, 4000,
                              reserve_usd=ctx.settings.summary_reserve_usd)
                res, returned = ctx.llm.web_search(q, WebSearchPostings, instructions=INSTRUCTIONS)
            except BudgetExceeded as e:
                ctx.log.warning("web search stopped by cost guard", extra={"reason": str(e)})
                break
            except NotImplementedError as e:
                raise SourceSkipped(str(e)) from e
            kept = 0
            for w in res.postings:
                if not acceptable(w.url, returned):
                    continue
                emp = map_employment(w.employment_type)
                out.append(RawPosting(
                    source="web_search", url=w.url, title=w.title, company=w.company, location=w.location,
                    work_mode=w.work_mode, employment_type=emp if emp != "unknown" else detect_employment_type(w.title),
                    posted_date=w.posted_date, description_text=w.description))
                kept += 1
            ctx.store.record_search(self.name, key, kept)
        return out
