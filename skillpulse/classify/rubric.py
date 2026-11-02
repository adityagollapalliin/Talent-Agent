"""Loads memory/LEVEL_RUBRIC.md (the editable level rules) at run time."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}
_ABBREV = {"sr": "senior", "snr": "senior", "jr": "junior"}


def normalise_title(title: str) -> str:
    """Lower-case, punctuation → spaces, split letter/digit runs, roman numerals → digits, Sr/Jr expanded."""
    s = title.lower()
    s = re.sub(r"[^a-z0-9+]+", " ", s)
    s = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", s)
    toks = [_ABBREV.get(t, _ROMAN.get(t, t)) for t in s.split()]
    return " ".join(toks)


@dataclass
class Band:
    level: str
    years_min: float | None
    years_max: float | None  # None = open-ended
    signals: list[str] = field(default_factory=list)  # normalised


@dataclass
class Rubric:
    bands: list[Band]
    text: str

    @property
    def levels(self) -> list[str]:
        return [b.level for b in self.bands]

    def rank(self, level: str) -> int:
        return self.levels.index(level) if level in self.levels else -1

    def band(self, level: str) -> Band | None:
        return next((b for b in self.bands if b.level == level), None)


def _num(s: str) -> float | None:
    s = s.strip()
    return float(s) if re.fullmatch(r"\d+(\.\d+)?", s) else None


def parse_rubric(md: str) -> Rubric:
    bands: list[Band] = []
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not re.fullmatch(r"L\d\+?", cells[0]):
            continue
        sigs = [normalise_title(x) for x in cells[3].split(",") if x.strip()]
        bands.append(Band(cells[0], _num(cells[1]), _num(cells[2]), sigs))
    if not bands:
        raise ValueError("LEVEL_RUBRIC.md has no level table rows")
    return Rubric(bands=bands, text=md)


def load_rubric(path: Path) -> Rubric:
    return parse_rubric(path.read_text(encoding="utf-8"))
