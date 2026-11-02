"""Level classification: regex years → title signals → LLM fallback (rules in memory/LEVEL_RUBRIC.md)."""

from __future__ import annotations

from collections.abc import Callable

from skillpulse.classify.rubric import Rubric, normalise_title
from skillpulse.classify.years import YearsResult, parse_years
from skillpulse.models import LevelResult

# (title, description, rubric) -> LevelResult | None  (None = unavailable / over budget)
LLMClassifier = Callable[[str, str, Rubric], LevelResult | None]


def years_to_level(yr: YearsResult, rubric: Rubric) -> tuple[str, bool]:
    """Map years_min to a band (skipping title-only bands like L0). Returns (level, ambiguous)."""
    bands = [b for b in rubric.bands if b.years_min is not None and not (b.years_max == b.years_min)]
    chosen = bands[-1]
    for b in bands:
        hi = b.years_max if b.years_max is not None else float("inf")
        if b.years_min <= yr.years_min < hi:
            chosen = b
            break
    ambiguous = (
        yr.years_max is not None and chosen.years_max is not None and yr.years_max > chosen.years_max
    )
    return chosen.level, ambiguous


def title_level(title: str, rubric: Rubric) -> tuple[str | None, list[str]]:
    """Highest level whose signal appears as whole words in the normalised title."""
    t = f" {normalise_title(title)} "
    hits: list[tuple[int, str, str]] = []
    for b in rubric.bands:
        for sig in b.signals:
            if sig and f" {sig} " in t:
                hits.append((rubric.rank(b.level), b.level, sig))
    if not hits:
        return None, []
    best = max(hits)[1]
    return best, [f"title:{sig}→{lvl}" for _, lvl, sig in sorted(hits)]


def classify(
    title: str,
    description: str,
    rubric: Rubric,
    employment_type: str = "unknown",
    llm: LLMClassifier | None = None,
) -> LevelResult | None:
    """Returns None only when regex + title fail and the LLM is unavailable (retry next run)."""
    if employment_type == "internship":
        return LevelResult("L0", "high", signals_used=["employment:internship"])

    yr = parse_years(description) or parse_years(title)
    t_level, t_signals = title_level(title, rubric)

    y_level, ambiguous = (years_to_level(yr, rubric) if yr else (None, False))
    signals = list(t_signals)
    if yr:
        signals.insert(0, f"years:'{yr.matched.strip()}'→{y_level}" + (" (ambiguous)" if ambiguous else ""))
    ymin = yr.years_min if yr else None
    ymax = yr.years_max if yr else None

    if t_level and y_level:
        if t_level == y_level:
            return LevelResult(t_level, "high", ymin, ymax, signals, ambiguous)
        signals.append(f"conflict: title {t_level} vs years {y_level}; title wins")
        return LevelResult(t_level, "low", ymin, ymax, signals, ambiguous)
    if t_level:
        return LevelResult(t_level, "medium", ymin, ymax, signals)
    if y_level:
        return LevelResult(y_level, "low" if ambiguous else "medium", ymin, ymax, signals, ambiguous)

    if llm is None:
        return None
    res = llm(title, description, rubric)
    if res is None:
        return None
    if res.level not in rubric.levels:
        return None
    res.signals_used = ["llm"] + res.signals_used
    return res
