"""Regex parser for years-of-experience requirements. No LLM cost."""

from __future__ import annotations

import re
from dataclasses import dataclass

_N = r"(\d{1,2}(?:\.\d)?)"
_U = r"\s*(?:years?|yrs?)\b"
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # 2-5 years, 5 to 7 yrs, 3 – 6+ years
    ("range", re.compile(_N + r"\s*\+?\s*(?:-|–|—|to)\s*" + _N + r"\s*\+?" + _U, re.I)),
    # minimum 4 years, at least 3 yrs, min. of 2 years
    ("min", re.compile(r"(?:minimum|min\.?|at\s*least|atleast)\s*(?:of\s*)?" + _N + r"\s*\+?" + _U, re.I)),
    # 3+ years
    ("plus", re.compile(_N + r"\s*\+" + _U, re.I)),
    # 4 years or more / 4 yrs and above
    ("plus", re.compile(_N + _U + r"\s*(?:or\s+more|and\s+above|plus|or\s+above)", re.I)),
    # 3 years of experience / 1 year exp
    ("plain", re.compile(_N + _U + r"\s*(?:of\s+)?(?:\w+\s+){0,3}?(?:experience|exp)\b", re.I)),
]
_FRESHER = re.compile(r"\bfreshers?\b|\bentry[\s-]level\b|\bno (?:prior )?experience (?:is )?required\b", re.I)
_CONTEXT = re.compile(r"experience|\bexp\b|expertise|background|work(?:ing)? in", re.I)
MAX_PLAUSIBLE = 20  # "25 years in business" is about the company, not the candidate


@dataclass
class YearsResult:
    years_min: float
    years_max: float | None
    matched: str


def parse_years(text: str) -> YearsResult | None:
    """Return the experience requirement in `text`.

    Collects every match and drops overlaps (range > min > plus > plain). Prefers matches with
    'experience' within 80 chars, then takes the first in document order (requirements usually come first).
    """
    if not text:
        return None
    found: list[tuple[int, int, int, YearsResult]] = []  # (start, end, priority, result)
    for prio, (kind, pat) in enumerate(_PATTERNS):
        for m in pat.finditer(text):
            if kind == "range":
                lo, hi = float(m.group(1)), float(m.group(2))
                if hi < lo:
                    lo, hi = hi, lo
                res = YearsResult(lo, hi, m.group(0))
            else:
                res = YearsResult(float(m.group(1)), None, m.group(0))
            if res.years_min > MAX_PLAUSIBLE:
                continue
            if any(not (m.end() <= s or m.start() >= e) and p <= prio for s, e, p, _ in found):
                continue
            found = [f for f in found if (m.end() <= f[0] or m.start() >= f[1])]
            found.append((m.start(), m.end(), prio, res))
    if found:
        def has_ctx(f: tuple[int, int, int, YearsResult]) -> bool:
            return bool(_CONTEXT.search(text[max(0, f[0] - 80): f[1] + 80]))

        ctx = [f for f in found if has_ctx(f)]
        pool = ctx or found
        pool.sort(key=lambda f: f[0])
        return pool[0][3]
    m = _FRESHER.search(text)
    if m:
        return YearsResult(0.0, 1.0, m.group(0))
    return None
