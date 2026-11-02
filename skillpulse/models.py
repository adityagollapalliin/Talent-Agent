"""Core data types shared across components."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]
EmploymentType = Literal["full-time", "internship", "contract", "part-time", "unknown"]
Confidence = Literal["high", "medium", "low"]


@dataclass
class RawPosting:
    """What every Source returns. Normalised, but not yet classified or extracted."""

    source: str
    url: str
    title: str
    company: str
    location: str
    work_mode: WorkMode = "unknown"
    employment_type: EmploymentType = "unknown"
    posted_date: str | None = None  # ISO date (YYYY-MM-DD) when known
    description_text: str = ""
    # Set by the pipeline / source when known:
    search_title: str | None = None
    role_family: str | None = None
    city: str | None = None


@dataclass
class LevelResult:
    level: str  # "L0".."L3" or "L4+"
    confidence: Confidence
    years_min: float | None = None
    years_max: float | None = None
    signals_used: list[str] = field(default_factory=list)
    ambiguous: bool = False


@dataclass
class SourceStatus:
    name: str
    ok: bool
    fetched: int = 0
    error: str | None = None
    skipped_reason: str | None = None
