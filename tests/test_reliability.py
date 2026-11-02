import json
import logging
import smtplib

import httpx
import pytest

from skillpulse.config import ROOT, Paths, Secrets, load_settings
from skillpulse.llm.base import CostTracker
from skillpulse.pipeline import run_daily
from skillpulse.util.lock import LockHeld, run_lock
from skillpulse.util.logging_setup import JsonFormatter, setup_logging
from skillpulse.util.retry import RateLimiter, request_json

S = load_settings(ROOT / "config.yaml")
SECRETS = Secrets(None, None, None, None, "me@zohomail.in", "pw", "me@zohomail.in", None)


def _paths(tmp):
    return Paths(root=ROOT, data=tmp / "data", reports=tmp / "reports", logs=tmp / "logs",
                 memory=ROOT / "memory", config_dir=ROOT / "config", fixtures=ROOT / "tests" / "fixtures")


def test_lock_prevents_overlap(tmp_path):
    lock = tmp_path / "x.lock"
    with run_lock(lock):
        with pytest.raises(LockHeld):
            with run_lock(lock):
                pass
    with run_lock(lock):  # released after exit
        pass


def test_dry_run_makes_no_network_or_email(monkeypatch, tmp_path):
    def no_network(self, request, **kw):
        raise AssertionError(f"dry run tried the network: {request.url}")

    def no_smtp(*a, **k):
        raise AssertionError("dry run tried to send email")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(smtplib, "SMTP_SSL", no_smtp)
    res = run_daily(_paths(tmp_path), S, SECRETS, day="2026-11-02", dry_run=True, memory_out=tmp_path)
    assert res.status == "success" and not res.emailed
    assert (tmp_path / "MEMORY.md").exists()


def test_cost_guard_caps_run_spend_and_reports_it(monkeypatch, tmp_path):
    """A paid model with a tiny budget: extraction stops early, the run still succeeds and reports deferrals."""
    import skillpulse.llm.factory as fac
    from skillpulse.llm.fake import FakeLLM

    def priced(settings, secrets, aliases, dry_run=False):
        llm = FakeLLM(aliases, CostTracker(max_usd=0.012, pricing={
            "fake-extract": {"input": 1.0, "output": 5.0}, "fake-summary": {"input": 1.0, "output": 5.0}}))
        return llm

    monkeypatch.setattr(fac, "build_llm", priced)
    settings = S.model_copy(deep=True)
    settings.summary_reserve_usd = 0.0
    res = run_daily(_paths(tmp_path), settings, SECRETS, day="2026-11-02", dry_run=True, memory_out=tmp_path)
    h = res.data["health"]
    assert res.status == "success"
    assert h["skipped_budget"] > 0 and h["extracted"] > 0
    assert h["cost_usd"] <= 0.012 + 1e-9


def test_json_logs_include_extra_fields(tmp_path):
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("w",), None)
    rec.source = "adzuna"
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "hello w" and out["source"] == "adzuna" and out["level"] == "INFO"
    setup_logging(tmp_path / "logs")
    logging.getLogger("t").info("to file")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "to file" in (tmp_path / "logs" / "skillpulse.log").read_text()
    setup_logging(tmp_path / "logs", "WARNING")  # leave root logger quiet for other tests


def test_request_json_retries_with_backoff(monkeypatch):
    import skillpulse.util.retry as r

    sleeps = []
    monkeypatch.setattr(r.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})

    out = request_json(httpx.Client(transport=httpx.MockTransport(handler)), "GET", "https://api.test/x")
    assert out == {"ok": True} and calls["n"] == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]


def test_request_json_does_not_retry_4xx():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        request_json(httpx.Client(transport=httpx.MockTransport(handler)), "GET", "https://api.test/x")
    assert calls["n"] == 1


def test_rate_limiter_spaces_calls(monkeypatch):
    import skillpulse.util.retry as r

    t = {"now": 100.0}
    slept = []
    monkeypatch.setattr(r.time, "monotonic", lambda: t["now"])
    monkeypatch.setattr(r.time, "sleep", lambda s: (slept.append(s), t.__setitem__("now", t["now"] + s)))
    lim = RateLimiter(2.0)
    lim.wait()
    lim.wait()
    assert slept == [2.0]
