from skillpulse.models import LevelResult, RawPosting
from skillpulse.store import SqliteStore
from skillpulse.util.text import description_hash, normalise_url

JD = "We are hiring a backend engineer. " * 20


def _p(url: str, desc: str = JD, title: str = "Software Engineer") -> RawPosting:
    return RawPosting(source="test", url=url, title=title, company="Acme", location="Hyderabad",
                      description_text=desc, city="Hyderabad", role_family="software_engineering")


def test_normalise_url_drops_tracking_and_fragment():
    a = normalise_url("https://WWW.Example.com/jobs/123/?utm_source=x&gh_jid=9#apply")
    b = normalise_url("https://example.com/jobs/123?gh_jid=9")
    assert a == b


def test_description_hash_is_whitespace_and_case_insensitive():
    assert description_hash("Hello   World\n") == description_hash("hello world")


def test_dedupe_by_url():
    s = SqliteStore(":memory:")
    pid1, new1 = s.upsert_posting(_p("https://x.com/j/1?utm_campaign=a"), "2026-11-01")
    pid2, new2 = s.upsert_posting(_p("https://x.com/j/1"), "2026-11-02")
    assert new1 and not new2 and pid1 == pid2
    row = s.get_posting(pid1)
    assert row["times_seen"] == 2 and row["last_seen"] == "2026-11-02" and row["first_seen"] == "2026-11-01"


def test_dedupe_by_description_hash_across_urls():
    s = SqliteStore(":memory:")
    pid1, _ = s.upsert_posting(_p("https://board-a.com/1"), "2026-11-01")
    pid2, new2 = s.upsert_posting(_p("https://board-b.com/zz", desc=JD.upper() + "   "), "2026-11-01")
    assert not new2 and pid1 == pid2


def test_short_descriptions_do_not_dedupe_by_hash():
    # Two different jobs with the same tiny snippet must not merge.
    s = SqliteStore(":memory:")
    _, n1 = s.upsert_posting(_p("https://a.com/1", desc="Apply now"), "2026-11-01")
    _, n2 = s.upsert_posting(_p("https://a.com/2", desc="Apply now"), "2026-11-01")
    assert n1 and n2


def test_extraction_cache_and_queue():
    s = SqliteStore(":memory:")
    pid, _ = s.upsert_posting(_p("https://x.com/1"), "2026-11-01")
    s.set_level(pid, LevelResult(level="L2", confidence="high", years_min=3))
    assert [r["id"] for r in s.postings_needing_extraction(["L1", "L2"])] == [pid]
    s.save_extraction(pid, description_hash(JD), "fake", "m", {"must_have_skills": ["Python"]}, 10, 5, 0.001)
    assert s.postings_needing_extraction(["L1", "L2"]) == []
    assert s.get_extraction_by_hash(description_hash(JD))["data"]["must_have_skills"] == ["Python"]


def test_snapshot_respects_ttl():
    s = SqliteStore(":memory:")
    s.upsert_posting(_p("https://x.com/old", desc="old " * 80), "2026-09-01")
    s.upsert_posting(_p("https://x.com/new", desc="new " * 80), "2026-11-01")
    snap = s.snapshot("2026-11-02", ttl_days=45)
    assert [r["url"] for r in snap] == ["https://x.com/new"]


def test_search_log():
    s = SqliteStore(":memory:")
    assert not s.search_done_recently("adzuna", "q1", 20)
    s.record_search("adzuna", "q1", 5)
    assert s.search_done_recently("adzuna", "q1", 20)


def test_backup_rotation(tmp_path):
    s = SqliteStore(tmp_path / "db.sqlite")
    d = tmp_path / "backups"
    for i in range(3):
        (d).mkdir(exist_ok=True)
        (d / f"skillpulse-2020-01-0{i}_000000.db").write_bytes(b"x")
    s.backup(d, keep=2)
    assert len(list(d.glob("skillpulse-*.db"))) == 2
