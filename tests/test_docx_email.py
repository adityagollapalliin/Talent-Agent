import json
import smtplib
from email import message_from_bytes
from pathlib import Path

import pytest
from docx import Document

from skillpulse.config import ROOT, Paths, Secrets, load_settings
from skillpulse.deliver import email as mail
from skillpulse.pipeline import run_daily
from skillpulse.report.docx_builder import build_docx

S = load_settings(ROOT / "config.yaml")
SECRETS = Secrets(None, None, None, None, "me@zohomail.in", "app-pass", "me@zohomail.in", None)


def _paths(tmp: Path) -> Paths:
    return Paths(root=ROOT, data=tmp / "data", reports=tmp / "reports", logs=tmp / "logs",
                 memory=ROOT / "memory", config_dir=ROOT / "config", fixtures=ROOT / "tests" / "fixtures")


@pytest.fixture(scope="module")
def dry(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("run")
    return run_daily(_paths(tmp), S, SECRETS, day="2026-11-02", dry_run=True, memory_out=tmp)


def test_dry_run_writes_all_outputs(dry):
    assert dry.status == "success" and not dry.emailed
    for k in ("md", "json", "docx"):
        assert dry.files[k].exists()
    d = json.loads(dry.files["json"].read_text())
    assert d["glance"]["total_postings"] == 12 and d["glance"]["levels"]["L0"] == 1


def test_docx_has_title_headings_and_real_tables(dry):
    doc = Document(str(dry.files["docx"]))
    texts = [p.text for p in doc.paragraphs]
    assert "SkillPulse" in texts
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert "1. Today at a glance" in headings and "Software Engineering" in headings
    assert "4. Sources and run health" in headings
    assert len(doc.tables) >= 10
    first_skill_table = next(t for t in doc.tables if t.rows[0].cells[1].text == "Skill")
    assert [c.text for c in first_skill_table.rows[0].cells] == ["#", "Skill", "Share", "vs 7d", "vs 30d"]
    assert not any("|---" in p.text for p in doc.paragraphs)  # no markdown pasted in
    assert b"w:hyperlink" in __import__("zipfile").ZipFile(dry.files["docx"]).read("word/document.xml")


def test_docx_arrows(tmp_path, dry):
    d = json.loads(dry.files["json"].read_text())
    lv = d["families"][0]["levels"][0]
    lv["top_skills"][0].update({"d7": 6.0, "arrow7": "↑", "d30": -3.0, "arrow30": "↓"})
    doc = Document(str(build_docx(d, tmp_path / "x.docx")))
    cells = [c.text for t in doc.tables for row in t.rows for c in row.cells]
    assert "↑ +6.0" in cells and "↓ -3.0" in cells


def test_report_message_shape(dry):
    msg = mail.build_report_message(dry.data, "me@zohomail.in", "me@zohomail.in", S.delivery.email, dry.files["docx"])
    assert msg["Subject"].startswith("SkillPulse · 2026-11-02 · 12 postings · Top riser: ")
    assert msg["From"] == "me@zohomail.in"
    parsed = message_from_bytes(msg.as_bytes())
    types = [p.get_content_type() for p in parsed.walk()]
    assert "text/plain" in types and "text/html" in types
    assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in types
    html = next(p for p in parsed.walk() if p.get_content_type() == "text/html").get_payload(decode=True).decode()
    assert 'name="viewport"' in html and "Software Engineering" in html


class FakeSMTP:
    sent: list = []
    logins: list = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        FakeSMTP.logins.append((self.host, self.port, u, p))

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_send_uses_smtp_ssl_zoho_in(monkeypatch, dry):
    FakeSMTP.sent, FakeSMTP.logins = [], []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    msg = mail.build_report_message(dry.data, "me@zohomail.in", "me@zohomail.in", S.delivery.email, None)
    mail.send(msg, S.delivery.email, SECRETS)
    assert FakeSMTP.logins == [("smtp.zoho.in", 465, "me@zohomail.in", "app-pass")]
    assert len(FakeSMTP.sent) == 1


def test_send_requires_config():
    with pytest.raises(mail.EmailNotConfigured):
        mail.check_configured(Secrets(None, None, None, None, None, None, None, None))


def test_failed_run_sends_failure_email(monkeypatch, tmp_path):
    """A crash mid-run must still email the owner, with the error and the last successful run date."""
    FakeSMTP.sent, FakeSMTP.logins = [], []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    import skillpulse.pipeline as pl

    def boom(*a, **k):
        raise RuntimeError("aggregation exploded")

    monkeypatch.setattr(pl, "build_report", boom)
    paths = _paths(tmp_path)
    # Not a dry run (dry runs never email); fixture sources via monkeypatched build_sources; fake LLM via factory.
    from skillpulse.sources.fixtures import FixtureSource
    monkeypatch.setattr(pl, "build_sources", lambda s, d: [FixtureSource()])
    import skillpulse.llm.factory as fac
    from skillpulse.llm.fake import FakeLLM
    monkeypatch.setattr(fac, "build_llm", lambda s, sec, a, dry_run=False: FakeLLM(a))
    with pytest.raises(RuntimeError):
        run_daily(paths, S, SECRETS, day="2026-11-03", dry_run=False, memory_out=tmp_path)
    assert len(FakeSMTP.sent) == 1
    m = FakeSMTP.sent[0]
    assert m["Subject"] == "SkillPulse · 2026-11-03 · RUN FAILED"
    body = m.get_body(preferencelist=("plain",)).get_content()
    assert "aggregation exploded" in body and "Last successful run: never" in body
