import json
from pathlib import Path

import httpx
import pytest

from skillpulse.classify.rubric import load_rubric
from skillpulse.config import ROOT, Paths, Secrets, load_settings
from skillpulse.pipeline import classify_pending, collect
from skillpulse.sources.adzuna import AdzunaSource, parse_results
from skillpulse.sources.base import FetchContext
from skillpulse.sources.detect import detect_employment_type, detect_work_mode, map_employment
from skillpulse.sources.fixtures import FixtureSource
from skillpulse.sources.matching import LocationFilter, TitleMatcher
from skillpulse.store import SqliteStore

FIX = Path(__file__).parent / "fixtures"
SETTINGS = load_settings(ROOT / "config.yaml")
NO_SECRETS = Secrets(None, None, None, None, None, None, None, None)


def ctx(store, http=None, secrets=NO_SECRETS):
    return FetchContext(settings=SETTINGS, secrets=secrets, paths=Paths.at(ROOT), store=store,
                        http=http or httpx.Client())


def test_adzuna_parse_results():
    payload = json.loads((FIX / "adzuna_search.json").read_text())
    got = parse_results(payload, "Software Engineer 2", "software_engineering")
    assert len(got) == 3
    p = got[0]
    assert p.company == "Fintech Labs Pvt Ltd" and p.employment_type == "full-time"
    assert p.work_mode == "hybrid" and p.posted_date == "2026-10-30"
    assert got[1].employment_type == "internship"


def test_adzuna_end_to_end_with_mock_transport():
    payload = json.loads((FIX / "adzuna_search.json").read_text())
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url)
        return httpx.Response(200, json=payload)

    store = SqliteStore(":memory:")
    secrets = Secrets(None, None, "id", "key", None, None, None, None)
    c = ctx(store, httpx.Client(transport=httpx.MockTransport(handler)), secrets)
    src = AdzunaSource()
    src_cfg = SETTINGS.source("adzuna")
    src_cfg.min_interval_s = 0
    st = collect([src], c, "2026-11-02")
    assert st.statuses[0].ok and st.fetched == len(seen) * 3
    # Sales job filtered by title; the SWE II + intern are kept (same 2 URLs every query → deduped)
    assert st.new == 2 and st.off_target_title == len(seen)
    assert "api.adzuna.com/v1/api/jobs/in/search/1" in str(seen[0])
    # Second run on the same day: search cache means zero HTTP calls
    before = len(seen)
    collect([src], c, "2026-11-02")
    assert len(seen) == before
    counts = classify_pending(store, SETTINGS, load_rubric(ROOT / "memory" / "LEVEL_RUBRIC.md"), None)
    assert counts == {"L2": 1, "L0": 1}


def test_adzuna_skipped_without_keys():
    st = collect([AdzunaSource()], ctx(SqliteStore(":memory:")), "2026-11-02")
    assert st.statuses[0].skipped_reason and st.statuses[0].ok


def test_failing_source_does_not_stop_run():
    class Boom:
        name = "boom"

        def fetch(self, ctx):
            raise RuntimeError("API down")

    store = SqliteStore(":memory:")
    st = collect([Boom(), FixtureSource()], ctx(store), "2026-11-02")
    assert not st.statuses[0].ok and "API down" in st.statuses[0].error
    assert st.statuses[1].ok and st.new > 0


def test_fixture_collect_filters_and_dedupes():
    store = SqliteStore(":memory:")
    st = collect([FixtureSource()], ctx(store), "2026-11-02")
    assert st.fetched == 14
    assert st.outside_region == 1  # onsite Pune
    assert st.cached == 1  # duplicate URL with utm param
    assert st.new == 12


@pytest.mark.parametrize("texts,mode", [
    (("Software Engineer (Remote)",), "remote"),
    (("Engineer", "Hyderabad", "Hybrid - 3 days in office"), "hybrid"),
    (("Engineer", "Pune", "Work from office, 5 days"), "onsite"),
    (("Engineer", "", "This is not a remote role. On-site."), "onsite"),
    (("Engineer", "", ""), "unknown"),
])
def test_detect_work_mode(texts, mode):
    assert detect_work_mode(*texts) == mode


def test_detect_employment_type():
    assert detect_employment_type("Data Science Intern") == "internship"
    assert detect_employment_type("Data Analyst", "Internal tools team. Full-time role.") == "full-time"
    assert detect_employment_type("BI Analyst", "6-month contract role") == "contract"
    assert detect_employment_type("Engineer", "Great team") == "unknown"
    assert map_employment("FullTime") == "full-time" and map_employment("Intern") == "internship"


@pytest.mark.parametrize("title,family", [
    ("Senior Software Engineer, Backend", "software_engineering"),
    ("SDE-II", "software_engineering"),
    ("Forward Deployed Engineer - India", "forward_deployed_ai"),
    ("GenAI Engineer", "machine_learning_data_science"),
    ("Sr. Data Analyst", "data_analytics"),
    ("Site Reliability Engineer III", "devops_cloud"),
    ("Senior Sales Executive", None),
])
def test_title_matcher(title, family):
    m = TitleMatcher.from_settings(SETTINGS).match(title)
    assert (m[0] if m else None) == family


def test_location_city():
    lf = LocationFilter(SETTINGS)
    assert lf.city("Hyderabad, Telangana, India") == "Hyderabad"
    assert lf.city("Secunderabad") == "Hyderabad"
    assert lf.city("Bengaluru, Karnataka") == "Bengaluru"
    assert lf.city("Remote - India") is None
    assert lf.city("London, UK") is None
