"""Work-mode and employment-type detection from posting text. Unknown when not found."""

from __future__ import annotations

import re

from skillpulse.models import EmploymentType, WorkMode

_HYBRID = re.compile(r"\bhybrid\b", re.I)
_REMOTE = re.compile(r"\b(?:remote|work from home|wfh|work-from-home|fully distributed)\b", re.I)
_NOT_REMOTE = re.compile(r"\b(?:not (?:a )?remote|no remote|non[- ]remote)\b", re.I)
_ONSITE = re.compile(r"\b(?:on[- ]?site|in[- ]office|work from office|wfo|office[- ]based)\b", re.I)

_INTERN = re.compile(r"\b(?:intern|interns|internship|trainee|apprentice(?:ship)?)\b", re.I)
_CONTRACT = re.compile(r"\b(?:contract(?:ual)?|freelance|c2h|contract[- ]to[- ]hire|temporary|fixed[- ]term)\b", re.I)
_PART = re.compile(r"\bpart[- ]time\b", re.I)
_FULL = re.compile(r"\b(?:full[- ]time|permanent|fte)\b", re.I)


def detect_work_mode(*texts: str | None) -> WorkMode:
    """Check title/location first (more specific), then the description."""
    for t in texts:
        if not t:
            continue
        if _HYBRID.search(t):
            return "hybrid"
        if _REMOTE.search(t) and not _NOT_REMOTE.search(t):
            return "remote"
        if _ONSITE.search(t):
            return "onsite"
    return "unknown"


def detect_employment_type(title: str, description: str = "") -> EmploymentType:
    """Title is authoritative for internships; the description can mention 'interns' in passing."""
    if _INTERN.search(title):
        return "internship"
    if _CONTRACT.search(title):
        return "contract"
    if _PART.search(title):
        return "part-time"
    head = description[:1500]
    if _FULL.search(head):
        return "full-time"
    if re.search(r"\binternship\b", head, re.I):
        return "internship"
    if _CONTRACT.search(head):
        return "contract"
    return "unknown"


def map_employment(value: str | None) -> EmploymentType:
    """Map structured ATS/API values (FullTime, full_time, Contract, Intern…)."""
    v = (value or "").lower().replace("_", "-").replace(" ", "-")
    if not v:
        return "unknown"
    if "intern" in v:
        return "internship"
    if "contract" in v or "temp" in v or "freelance" in v:
        return "contract"
    if "part" in v:
        return "part-time"
    if "full" in v or "permanent" in v or v == "fte":
        return "full-time"
    return "unknown"
