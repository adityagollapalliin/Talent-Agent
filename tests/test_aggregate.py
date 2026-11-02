from skillpulse.config import ROOT, load_settings
from skillpulse.extract.aliases import Aliases
from skillpulse.models import LevelResult, RawPosting
from skillpulse.report.aggregate import Baselines, aggregate_rows, arrow, build_buckets, build_report_data
from skillpulse.report.markdown import render_markdown
from skillpulse.report.prose import write_prose
from skillpulse.store import SqliteStore

S = load_settings(ROOT / "config.yaml")
A = Aliases.load(ROOT / "memory" / "skill_aliases.yaml", S.focus_list())
FAM = "software_engineering"


def ext(must, nice=(), certs=(), traits=()):
    return {"must_have_skills": list(must), "nice_to_have_skills": list(nice), "certifications": list(certs),
            "soft_skills_and_expectations": list(traits), "matches_focus_technologies": A.focus_hits(list(must) + list(nice))}


def seed(store, day, specs, level="L2", city="Hyderabad", mode="hybrid", start=0):
    for i, e in enumerate(specs):
        pid, _ = store.upsert_posting(RawPosting("t", f"https://x.com/{day}/{start + i}", "SDE 2", "Acme", city,
                                                 work_mode=mode, description_text=f"{day} {start + i} " * 60,
                                                 role_family=FAM, city=city), day)
        store.set_level(pid, LevelResult(level, "high"))
        store.save_extraction(pid, "h", "fake", "m", e, 0, 0, 0)


def test_shares_count_each_posting_once():
    s = SqliteStore(":memory:")
    seed(s, "2026-11-02", [ext(["Python", "SQL"], ["Python"]), ext(["Python"]), ext(["Java"]), ext([], ["Docker"])])
    b = [x for x in build_buckets(s.snapshot("2026-11-02", 45), S) if x.family == FAM and x.level == "L2"][0]
    rows = {(r["category"], r["item"]): r for r in aggregate_rows([b], A)}
    assert rows[("skill", "Python")]["share"] == 50.0  # 2 of 4, the must+nice double mention counts once
    assert rows[("must", "Python")]["count"] == 2
    assert ("nice", "Python") not in rows  # nice excludes items already must-have in the same posting
    assert rows[("nice", "Docker")]["share"] == 25.0
    assert rows[("_n", "_n")]["count"] == 4


def test_baseline_mean_counts_absent_days_as_zero():
    s = SqliteStore(":memory:")
    s.save_daily_aggregates("2026-10-30", [
        {"family": FAM, "level": "L2", "category": "_n", "item": "_n", "count": 10, "n_postings": 10, "share": 100},
        {"family": FAM, "level": "L2", "category": "skill", "item": "Python", "count": 4, "n_postings": 10, "share": 40.0}])
    s.save_daily_aggregates("2026-10-31", [
        {"family": FAM, "level": "L2", "category": "_n", "item": "_n", "count": 10, "n_postings": 10, "share": 100}])
    b = Baselines(s, "2026-11-02", 7)
    assert b.share(FAM, "L2", "skill", "Python") == 20.0  # (40 + 0) / 2 days
    assert b.share(FAM, "L2", "skill", "Rust") == 0.0
    assert b.share(FAM, "L3", "skill", "Python") is None  # no data for that bucket
    # window excludes today and anything older than 7 days
    assert Baselines(s, "2026-11-10", 7).share(FAM, "L2", "skill", "Python") is None


def test_arrows():
    assert arrow(None, 1) == "–" and arrow(2.5, 1) == "↑" and arrow(-1.5, 1) == "↓" and arrow(0.5, 1) == "="


def test_report_deltas_and_rising_end_to_end():
    s = SqliteStore(":memory:")
    # Day 1: 4 postings, Python 50%, Kubernetes 0%
    seed(s, "2026-10-31", [ext(["Python"]), ext(["Python"]), ext(["Java"]), ext(["Java"])])
    s.save_daily_aggregates("2026-10-31", aggregate_rows(build_buckets(s.snapshot("2026-10-31", 45), S), A))
    # Day 2 adds 4 postings → snapshot of 8: Python 3/8 = 37.5%, Kubernetes 3/8 = 37.5% (new)
    seed(s, "2026-11-02", [ext(["Python", "Kubernetes"]), ext(["Kubernetes"]), ext(["Kubernetes"]), ext(["Go"])], start=10)
    data = build_report_data("2026-11-02", s.snapshot("2026-11-02", 45), s, S, A, {"sources": []})
    lv = next(x for f in data["families"] if f["family"] == FAM for x in f["levels"] if x["level"] == "L2")
    sk = {x["skill"]: x for x in lv["top_skills"]}
    assert lv["n"] == 8
    assert sk["Python"]["share"] == 37.5 and sk["Python"]["d7"] == -12.5 and sk["Python"]["arrow7"] == "↓"
    assert sk["Kubernetes"]["d7"] == 37.5 and sk["Kubernetes"]["arrow7"] == "↑"
    rising = {r["skill"]: r for r in lv["rising"]}
    assert rising["Kubernetes"]["new"] and "Python" not in rising
    assert data["glance"]["top_rising"][0]["skill"] == "Kubernetes"
    assert data["glance"]["new_today"] == 4 and data["glance"]["total_postings"] == 8
    focus = {f["tech"]: f for f in lv["focus_table"]}
    assert focus["Kubernetes"]["must"] == 37.5 and focus["Python"]["must"] == 37.5
    md = render_markdown(write_prose(data, None))
    assert "Kubernetes" in md and "↑ +37.5" in md


def test_location_split_and_level_counts():
    s = SqliteStore(":memory:")
    seed(s, "2026-11-02", [ext(["Python"])] * 2, city="Hyderabad", mode="hybrid")
    seed(s, "2026-11-02", [ext(["Python"])], city=None, mode="remote", start=5)
    seed(s, "2026-11-02", [ext(["Python"])], city="Pune", mode="hybrid", start=9)
    seed(s, "2026-11-02", [ext(["Python"])], level="L4+", start=20)
    g = build_report_data("2026-11-02", s.snapshot("2026-11-02", 45), s, S, A, {"sources": []})["glance"]
    assert g["split"]["preferred_total"] == 3 and g["split"]["preferred_hybrid"] == 3
    assert g["split"]["india_remote"] == 1 and g["split"]["hybrid_elsewhere"] == 1
    assert g["levels"]["L4+"] == 1 and g["levels"]["L2"] == 4


def test_no_baseline_first_run():
    s = SqliteStore(":memory:")
    seed(s, "2026-11-02", [ext(["Python"])])
    d = build_report_data("2026-11-02", s.snapshot("2026-11-02", 45), s, S, A, {"sources": []})
    assert d["glance"]["top_rising"] == [] and not d["glance"]["baseline_available"]
    assert "No baseline yet" in render_markdown(write_prose(d, None))
