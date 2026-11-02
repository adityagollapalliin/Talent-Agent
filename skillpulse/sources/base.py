"""Source plugin interface. Each source returns normalised RawPosting objects.

Rule: only official APIs and public ATS job-board APIs. Never scrape LinkedIn, Naukri, Indeed,
Glassdoor, Instahyre or Wellfound HTML.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import httpx

from skillpulse.config import Paths, Secrets, Settings
from skillpulse.models import RawPosting
from skillpulse.store.base import Store

if TYPE_CHECKING:
    from skillpulse.llm.base import LLMClient

BANNED_DOMAINS = ("linkedin.com", "naukri.com", "indeed.com", "indeed.co.in", "glassdoor.com",
                  "glassdoor.co.in", "instahyre.com", "wellfound.com", "angel.co")


@dataclass
class FetchContext:
    settings: Settings
    secrets: Secrets
    paths: Paths
    store: Store
    http: httpx.Client
    llm: "LLMClient | None" = None
    already_fetched: int = 0  # postings gathered by earlier sources this run
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("skillpulse.sources"))

    def all_titles(self) -> list[tuple[str, str]]:
        return [(fam, t) for fam, rf in self.settings.role_families.items() for t in rf.titles]


class SourceSkipped(Exception):
    """Raised when a source is configured off or lacks credentials (not a failure)."""


class Source(Protocol):
    name: str

    def fetch(self, ctx: FetchContext) -> list[RawPosting]: ...
