"""Title → role family matching, and the India / Hyderabad location filter. Both are driven by config."""

from __future__ import annotations

import re
from dataclasses import dataclass

from skillpulse.classify.rubric import normalise_title
from skillpulse.config import Settings
from skillpulse.models import RawPosting


@dataclass
class TitleMatcher:
    """Longest configured title contained (whole words) in the posting title wins."""

    entries: list[tuple[str, str, str]]  # (normalised title, family, original title), longest first

    @classmethod
    def from_settings(cls, s: Settings) -> "TitleMatcher":
        entries = [
            (normalise_title(t), fam, t) for fam, rf in s.role_families.items() for t in rf.titles
        ]
        entries.sort(key=lambda e: -len(e[0]))
        return cls(entries)

    def match(self, title: str) -> tuple[str, str] | None:
        """Returns (family, matched configured title) or None."""
        t = f" {normalise_title(title)} "
        for norm, fam, orig in self.entries:
            if f" {norm} " in t:
                return fam, orig
        return None


class LocationFilter:
    def __init__(self, s: Settings):
        self.preferred = s.region.preferred_city
        self.include_remote = s.region.include_india_remote
        self.india = [x for x in s.region.india_locations]
        self._pat = re.compile(r"\b(" + "|".join(re.escape(x) for x in self.india) + r")\b", re.I)
        self._preferred_aliases = {self.preferred.lower(), "secunderabad"} if self.preferred.lower() == "hyderabad" \
            else {self.preferred.lower()}

    def city(self, location: str) -> str | None:
        """Most specific India location mentioned (preferred city wins if present)."""
        hits = [m.group(1) for m in self._pat.finditer(location or "")]
        if not hits:
            return None
        if any(h.lower() in self._preferred_aliases for h in hits):
            return self.preferred
        specific = [h for h in hits if h.lower() not in ("india", "telangana")]
        return specific[0].title() if specific else None

    def in_india(self, p: RawPosting, assume_india: bool = False) -> bool:
        if assume_india:
            return True
        if self._pat.search(p.location or ""):
            return True
        # "Remote" with the country only stated in the text, e.g. "This role is remote (India)"
        return p.work_mode == "remote" and bool(re.search(r"\bindia\b", p.description_text[:3000], re.I))

    def keep(self, p: RawPosting) -> bool:
        """Hyderabad (any mode) + India-remote + hybrid anywhere in India."""
        if p.city == self.preferred:
            return True
        if p.work_mode == "remote" and self.include_remote:
            return True
        return p.work_mode == "hybrid"
