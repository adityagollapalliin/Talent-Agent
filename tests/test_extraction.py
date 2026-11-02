from types import SimpleNamespace

from skillpulse.config import ROOT, load_settings
from skillpulse.extract.aliases import Aliases
from skillpulse.extract.extractor import extract_pending, postprocess
from skillpulse.extract.schema import JobExtraction
from skillpulse.llm.base import BudgetExceeded, CostTracker
from skillpulse.llm.openai_client import OpenAIClient
from skillpulse.models import LevelResult, RawPosting
from skillpulse.store import SqliteStore

S = load_settings(ROOT / "config.yaml")
A = Aliases.load(ROOT / "memory" / "skill_aliases.yaml", S.focus_list())
PRICING = S.llm.openai.pricing


def _ext(**over):
    base = dict(must_have_skills=["python", "k8s", "GCP"], nice_to_have_skills=["Pinecone"], certifications=["CKA"],
                languages=["Python"], frameworks=[], cloud=["GCP"], ai_platforms=["Claude"], data_platforms=[],
                tools=["k8s"], soft_skills_and_expectations=["Ownership mindset."], education_requirement=None,
                years_min=2, years_max=4, work_mode="hybrid", employment_type="full-time", city="Hyderabad",
                matches_focus_technologies=["Docker", "Not a focus tech"])
    base.update(over)
    return JobExtraction(**base)


class FakeResponses:
    """Stands in for openai.OpenAI().responses; returns a recorded-shape ParsedResponse."""

    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(output_parsed=self.parsed, usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
                               status="completed", output=[])


def _client(parsed, max_usd=1.0):
    fake = SimpleNamespace(responses=FakeResponses(parsed))
    c = OpenAIClient("sk-test", "gpt-6-luna", "gpt-6.1-sol", CostTracker(max_usd, PRICING), client=fake)
    return c, fake.responses


def _store_with(n):
    s = SqliteStore(":memory:")
    for i in range(n):
        pid, _ = s.upsert_posting(RawPosting("t", f"https://x.com/{i}", "SDE 2", "Acme", "Hyderabad",
                                             description_text=f"Job {i} " + "python kubernetes " * 40), "2026-11-02")
        s.set_level(pid, LevelResult("L2", "high"))
    return s


def test_postprocess_normalises_and_recomputes_focus():
    d = postprocess(_ext(), A)
    assert d["must_have_skills"] == ["Python", "Kubernetes", "Google Cloud Platform"]
    assert d["ai_platforms"] == ["Anthropic / Claude"]
    assert d["certifications"] == ["Certified Kubernetes Administrator (CKA)"]
    # Python finds Vector databases (via Pinecone) etc.; LLM's "Docker" is a focus item so is kept; junk dropped
    assert "Vector databases" in d["matches_focus_technologies"]
    assert "Docker" in d["matches_focus_technologies"]
    assert "Not a focus tech" not in d["matches_focus_technologies"]
    assert d["soft_skills_and_expectations"] == ["ownership mindset"]


def test_openai_client_uses_responses_parse_with_schema():
    c, r = _client(_ext())
    out = c.extract_structured("text", JobExtraction, instructions="x")
    assert isinstance(out, JobExtraction)
    call = r.calls[0]
    assert call["text_format"] is JobExtraction and call["model"] == "gpt-6-luna"
    assert c.tracker.input_tokens == 1200
    assert abs(c.tracker.spent_usd - (1200 * 0.10 + 300 * 0.50) / 1e6) < 1e-12


def test_extract_pending_caches_and_never_repeats():
    s = _store_with(3)
    c, r = _client(_ext())
    st = extract_pending(s, c, A, ["L1", "L2"], reserve_usd=0)
    assert st.extracted == 3 and len(r.calls) == 3
    st2 = extract_pending(s, c, A, ["L1", "L2"], reserve_usd=0)
    assert st2.extracted == 0 and len(r.calls) == 3


def test_cost_guard_stops_and_marks_skipped_budget():
    s = _store_with(5)
    c, r = _client(_ext(), max_usd=0.0013)  # ~2 calls' worth at gpt-6-luna prices
    st = extract_pending(s, c, A, ["L1", "L2"], reserve_usd=0)
    assert st.extracted < 5 and st.skipped_budget == 5 - st.extracted
    assert c.tracker.spent_usd <= 0.0013
    # Skipped postings are retried next run once budget allows
    c2, _ = _client(_ext(), max_usd=1.0)
    assert extract_pending(s, c2, A, ["L1", "L2"], reserve_usd=0).extracted == st.skipped_budget


def test_guard_raises():
    c, _ = _client(_ext(), max_usd=0.0)
    try:
        c.guard("extraction", 4000, 1000)
        raise AssertionError("expected BudgetExceeded")
    except BudgetExceeded:
        pass


def test_same_description_under_new_url_reuses_extraction():
    s = SqliteStore(":memory:")
    short = "Short JD python"  # short: hash dedupe off at upsert, but the extraction cache still matches the hash
    for i in range(2):
        pid, _ = s.upsert_posting(RawPosting("t", f"https://y.com/{i}", "SDE 2", "A", "Hyderabad", description_text=short), "2026-11-02")
        s.set_level(pid, LevelResult("L2", "high"))
    c, r = _client(_ext())
    st = extract_pending(s, c, A, ["L2"], reserve_usd=0)
    assert st.extracted == 1 and st.cache_hits == 1 and len(r.calls) == 1
