"""Fixture source for --dry-run and tests: reads tests/fixtures/postings_sample.json."""

from __future__ import annotations

import json

from skillpulse.models import RawPosting
from skillpulse.sources.base import FetchContext


class FixtureSource:
    name = "fixtures"

    def __init__(self, filename: str = "postings_sample.json"):
        self.filename = filename

    def fetch(self, ctx: FetchContext) -> list[RawPosting]:
        data = json.loads((ctx.paths.fixtures / self.filename).read_text(encoding="utf-8"))
        return [RawPosting(**{k: v for k, v in d.items() if k in RawPosting.__dataclass_fields__}) for d in data]
