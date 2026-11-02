import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from skillpulse.config import ROOT, Paths, Secrets, load_settings
from skillpulse.extract.schema import JobExtraction
from skillpulse.llm.anthropic_client import FALLBACK_BETA, WEB_SEARCH_TOOL, AnthropicClient
from skillpulse.llm.base import CostTracker, LLMRefusal
from skillpulse.pipeline import collect
from skillpulse.report.prose import IdealCandidate, ReportProse
from skillpulse.sources.aggregator import parse_serpapi
from skillpulse.sources.ats import AtsBoardsSource, parse_ashby, parse_greenhouse, parse_lever
from skillpulse.sources.base import FetchContext, SourceSkipped
from skillpulse.sources.websearch import WebPosting, WebSearchPostings, WebSearchSource, acceptable
from skillpulse.store import SqliteStore

FIX = Path(__file__).parent / "fixtures"
S = load_settings(ROOT / "config.yaml")
NOSEC = Secrets(None, None, None, None, None, None, None, None)


def fx(name):
    return json.loads((FIX / name).read_text())


def test_parse_greenhouse_html_to_text():
    jobs = parse_greenhouse(fx("greenhouse_jobs.json"), "Acme")
    p = jobs[0]
    assert p.title == "Software Engineer II, Payments" and p.location == "Hyderabad, India"
    assert "3+ years of experience" in p.description_text and "<" not in p.description_text
    assert p.work_mode == "hybrid" and p.posted_date == "2026-10-25"


def test_parse_lever_structured_fields():
    jobs = parse_lever(fx("lever_postings.json")["postings"], "Acme")
    assert jobs[0].work_mode == "remote" and jobs[0].employment_type == "full-time"
    assert "Advanced SQL" in jobs[0].description_text
    assert jobs[1].employment_type == "internship"


def test_parse_ashby_secondary_locations_and_unlisted():
    jobs = parse_ashby(fx("ashby_board.json"), "Acme")
    assert len(jobs) == 2  # unlisted dropped
    assert "Hyderabad" in jobs[0].location and jobs[0].work_mode == "hybrid"
    assert jobs[0].employment_type == "full-time"


def test_parse_serpapi_prefers_direct_link_and_skips_banned():
    jobs = parse_serpapi(fx("serpapi_google_jobs.json"), "Cloud Engineer")
    assert [j.url for j in jobs] == ["https://careers.acme.com/jobs/55"]


def test_ats_source_end_to_end(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "companies.yaml").write_text(
        "greenhouse:\n  - {name: Acme, token: acme}\n  - {name: Broken, token: nope}\n"
        "lever:\n  - {name: Acme, token: acme}\nashby:\n  - {name: Acme, token: acme}\n")

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "boards/nope" in u:
            return httpx.Response(404, json={"error": "not found"})
        if "greenhouse" in u:
            return httpx.Response(200, json=fx("greenhouse_jobs.json"))
        if "lever" in u:
            return httpx.Response(200, json=fx("lever_postings.json")["postings"])
        return httpx.Response(200, json=fx("ashby_board.json"))

    paths = Paths(root=tmp_path, data=tmp_path, reports=tmp_path, logs=tmp_path, memory=ROOT / "memory",
                  config_dir=tmp_path / "config", fixtures=FIX)
    S.source("ats_boards").min_interval_s = 0
    store = SqliteStore(":memory:")
    ctx = FetchContext(S, NOSEC, paths, store, httpx.Client(transport=httpx.MockTransport(handler)))
    st = collect([AtsBoardsSource()], ctx, "2026-11-02")
    status = st.statuses[0]
    assert status.ok and status.error.startswith("partial: 1 failed")  # broken board reported, run continues
    titles = {store.get_posting(i)["title"] for i in st.new_ids}
    # SWE II (Hyd), Data Analyst (remote India), DevOps intern (Hyd), FDE (Bengaluru hybrid).
    # Dropped: Account Executive (title), MLE San Francisco + AI Engineer US-remote (not India).
    assert titles == {"Software Engineer II, Payments", "Data Analyst", "DevOps Engineer Intern", "Forward Deployed Engineer"}


def test_websearch_acceptable_requires_returned_url():
    returned = {"https://jobs.lever.co/acme/123?utm_source=google"}
    assert acceptable("https://jobs.lever.co/acme/123", returned)
    assert not acceptable("https://jobs.lever.co/acme/999", returned)  # not returned by the search tool
    assert not acceptable("https://www.linkedin.com/jobs/view/1", {"https://www.linkedin.com/jobs/view/1"})
    assert not acceptable("https://careers.x.com/search?q=python", {"https://careers.x.com/search?q=python"})


class FakeSearchLLM:
    provider = "fake"

    def __init__(self):
        self.calls = 0

    def guard(self, *a, **k):
        pass

    def web_search(self, q, schema, instructions):
        self.calls += 1
        posting = WebPosting(url="https://jobs.ashbyhq.com/x/1", title="Data Analyst", company="X",
                             location="Hyderabad, India", work_mode="hybrid", employment_type="Full-time",
                             posted_date=None, description="SQL and Python. 1-2 years of experience.")
        fake = posting.model_copy(update={"url": "https://made-up.example/job"})
        return WebSearchPostings(postings=[posting, fake]), {"https://jobs.ashbyhq.com/x/1"}


def test_websearch_source_only_when_thin_and_drops_unreturned_urls():
    store = SqliteStore(":memory:")
    llm = FakeSearchLLM()
    ctx = FetchContext(S, NOSEC, Paths.at(ROOT), store, httpx.Client(), llm=llm)
    ctx.already_fetched = 999  # what collect() sets from earlier sources in the run
    with pytest.raises(SourceSkipped):
        WebSearchSource().fetch(ctx)
    assert llm.calls == 0
    ctx2 = FetchContext(S, NOSEC, Paths.at(ROOT), store, httpx.Client(), llm=llm)
    out = WebSearchSource().fetch(ctx2)
    assert llm.calls == len(S.role_families) and {p.url for p in out} == {"https://jobs.ashbyhq.com/x/1"}


# ------------------------------------------------------------------------------ Anthropic client (stubbed SDK)
def _usage(i=1000, o=200):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=0)


class StubMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def parse(self, **kw):
        self.calls.append(("parse", kw))
        return self.responses.pop(0)

    def create(self, **kw):
        self.calls.append(("create", kw))
        return self.responses.pop(0)


def _anthropic(responses, beta_responses=()):
    stub = SimpleNamespace(messages=StubMessages(responses), beta=SimpleNamespace(messages=StubMessages(beta_responses)))
    pricing = S.llm.anthropic.pricing
    return AnthropicClient("k", "claude-haiku-5-5", "claude-opus-5-5", CostTracker(1.0, pricing), client=stub), stub


def _job():
    return JobExtraction(must_have_skills=["Python"], nice_to_have_skills=[], certifications=[], languages=[],
                         frameworks=[], cloud=[], ai_platforms=[], data_platforms=[], tools=[],
                         soft_skills_and_expectations=[], education_requirement=None, years_min=None,
                         years_max=None, work_mode="unknown", employment_type="unknown", city=None,
                         matches_focus_technologies=[])


def test_anthropic_extraction_uses_messages_parse():
    c, stub = _anthropic([SimpleNamespace(parsed_output=_job(), stop_reason="end_turn", usage=_usage(),
                                          model="claude-haiku-5-5", content=[])])
    out = c.extract_structured("jd", JobExtraction, instructions="x")
    kind, kw = stub.messages.calls[0]
    assert kind == "parse" and kw["output_format"] is JobExtraction and kw["model"] == "claude-haiku-5-5"
    assert kw["output_config"] == {"effort": "low"} and out.must_have_skills == ["Python"]
    assert abs(c.tracker.spent_usd - (1000 * 0.10 + 200 * 0.50) / 1e6) < 1e-12


def test_anthropic_summary_uses_fallbacks_and_prices_served_model():
    prose = ReportProse(glance_paragraph="g", ideal_candidates=[IdealCandidate(bucket="a|L1", paragraph="p")])
    c, stub = _anthropic([], [SimpleNamespace(parsed_output=prose, stop_reason="end_turn", usage=_usage(),
                                              model="claude-opus-5-5", content=[])])
    c.extract_structured("{}", ReportProse, instructions="x", purpose="summary")
    kind, kw = stub.beta.messages.calls[0]
    assert kw["fallbacks"] == "default" and FALLBACK_BETA in kw["betas"] and kw["model"] == "claude-opus-5-5"


def test_anthropic_refusal_raises():
    c, _ = _anthropic([SimpleNamespace(parsed_output=None, stop_reason="refusal", usage=_usage(), model="m",
                                       stop_details=SimpleNamespace(category="cyber"), content=[])])
    with pytest.raises(LLMRefusal):
        c.extract_structured("jd", JobExtraction, instructions="x")


def test_anthropic_web_search_collects_urls_and_handles_pause_turn():
    r1 = SimpleNamespace(stop_reason="pause_turn", usage=_usage(), model="claude-opus-5-5", content=[
        SimpleNamespace(type="web_search_tool_result", content=[SimpleNamespace(url="https://jobs.lever.co/a/1")])])
    body = WebSearchPostings(postings=[]).model_dump_json()
    r2 = SimpleNamespace(stop_reason="end_turn", usage=_usage(), model="claude-opus-5-5", content=[
        SimpleNamespace(type="text", text=f"Here you go:\n```json\n{body}\n```")])
    c, stub = _anthropic([], [r1, r2])
    res, urls = c.web_search("q", WebSearchPostings, instructions="i")
    assert urls == {"https://jobs.lever.co/a/1"} and res.postings == []
    first = stub.beta.messages.calls[0][1]
    assert first["tools"][0]["type"] == WEB_SEARCH_TOOL and len(stub.beta.messages.calls) == 2
    assert stub.beta.messages.calls[1][1]["messages"][-1]["role"] == "assistant"
