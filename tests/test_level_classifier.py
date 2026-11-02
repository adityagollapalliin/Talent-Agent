from pathlib import Path

import pytest

from skillpulse.classify.classifier import classify, title_level, years_to_level
from skillpulse.classify.rubric import load_rubric, normalise_title
from skillpulse.classify.years import parse_years
from skillpulse.models import LevelResult

RUBRIC = load_rubric(Path(__file__).resolve().parent.parent / "memory" / "LEVEL_RUBRIC.md")


@pytest.mark.parametrize(
    "text,lo,hi",
    [
        ("We need 3+ years of experience in Python", 3, None),
        ("Experience: 2-5 yrs", 2, 5),
        ("minimum 4 years in backend development", 4, None),
        ("5 to 7 years of relevant experience", 5, 7),
        ("0-1 year experience, freshers welcome", 0, 1),
        ("Freshers can apply", 0, 1),
        ("3 – 6 Years experience", 3, 6),
        ("at least 2 yrs of hands-on experience", 2, None),
        ("1.5 years of experience with SQL", 1.5, None),
        ("6 years or more in data engineering", 6, None),
        ("Our company has 25 years of history. Requires 2+ years experience", 2, None),
    ],
)
def test_parse_years(text, lo, hi):
    r = parse_years(text)
    assert r is not None
    assert (r.years_min, r.years_max) == (lo, hi)


def test_parse_years_none():
    assert parse_years("Great team, great culture.") is None


def test_parse_years_prefers_experience_context():
    r = parse_years("Founded 10 years ago. You bring 4+ years of experience.")
    assert r.years_min == 4


def test_normalise_title():
    assert normalise_title("SDE-II") == "sde 2"
    assert normalise_title("Sr. Software Engineer") == "senior software engineer"
    assert normalise_title("SDE1") == "sde 1"


@pytest.mark.parametrize(
    "title,level",
    [
        ("SDE 2", "L2"),
        ("Software Engineer II", "L2"),
        ("SDE-1", "L1"),
        ("Junior Software Engineer", "L1"),
        ("Associate Data Analyst", "L1"),
        ("Senior Software Engineer", "L3"),
        ("Sr. DevOps Engineer", "L3"),
        ("Staff Engineer", "L4+"),
        ("Principal Data Scientist", "L4+"),
        ("Data Science Intern", "L0"),
        ("Senior Associate, Data Science", "L3"),  # highest match wins
        ("Software Engineer III", "L3"),
        ("Software Engineer", None),
        ("Machine Learning Engineer", None),
    ],
)
def test_title_level(title, level):
    assert title_level(title, RUBRIC)[0] == level


def test_title_signal_needs_whole_word():
    # "Lead" must not match inside "Leader"/"Leading"; "Head" not inside "Headless"
    assert title_level("Headless CMS Developer", RUBRIC)[0] is None


@pytest.mark.parametrize(
    "lo,hi,level,amb",
    [(0, 1, "L1", False), (0, 2, "L1", False), (2, 5, "L2", False), (3, 7, "L2", True),
     (5, 8, "L3", False), (8, None, "L4+", False), (1, 3, "L1", True), (10, 12, "L4+", False)],
)
def test_years_to_level(lo, hi, level, amb):
    from skillpulse.classify.years import YearsResult
    assert years_to_level(YearsResult(lo, hi, ""), RUBRIC) == (level, amb)


def test_years_and_title_agree_high_confidence():
    r = classify("Senior Backend Engineer", "Requires 5+ years of experience", RUBRIC)
    assert (r.level, r.confidence) == ("L3", "high")


def test_conflict_title_wins_low_confidence():
    r = classify("SDE 2", "We want 6+ years of experience", RUBRIC)
    assert r.level == "L2" and r.confidence == "low"
    assert r.years_min == 6 and any("conflict" in s for s in r.signals_used)


def test_years_only_plain_title():
    r = classify("Software Engineer", "2-5 years of experience with Java", RUBRIC)
    assert (r.level, r.confidence, r.years_min, r.years_max) == ("L2", "medium", 2, 5)


def test_ambiguous_range_low_confidence():
    r = classify("Software Engineer", "3-7 years experience", RUBRIC)
    assert (r.level, r.confidence, r.ambiguous) == ("L2", "low", True)


def test_internship_employment_type():
    assert classify("Software Engineer", "", RUBRIC, employment_type="internship").level == "L0"


def test_llm_fallback_only_when_both_fail():
    calls = []

    def fake(title, desc, rubric):
        calls.append(title)
        return LevelResult("L2", "medium", signals_used=["guess"])

    assert classify("Senior Engineer", "", RUBRIC, llm=fake).level == "L3"
    assert calls == []
    r = classify("Software Engineer", "Build things.", RUBRIC, llm=fake)
    assert r.level == "L2" and r.signals_used[0] == "llm" and calls == ["Software Engineer"]


def test_no_llm_returns_none():
    assert classify("Software Engineer", "Build things.", RUBRIC) is None


def test_llm_answer_outside_rubric_rejected():
    assert classify("X", "", RUBRIC, llm=lambda *a: LevelResult("L9", "low")) is None
