"""Zoho Mail delivery over SMTP_SSL (smtp.zoho.in:465 by default; host/port in config).

HTML body (mobile-friendly: inline styles, single column, 100%-width tables) + plain-text fallback + .docx attachment.
A failed run still sends a short failure email, so a report never silently goes missing.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any

from skillpulse.config import EmailConfig, Secrets

log = logging.getLogger("skillpulse.email")
DOCX_MIME = ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")


class EmailNotConfigured(Exception):
    pass


def subject_for(d: dict[str, Any], prefix: str = "") -> str:
    g = d["glance"]
    riser = g["top_rising"][0]["skill"] if g["top_rising"] else "—"
    return f"{prefix}SkillPulse · {d['date']} · {g['total_postings']} postings · Top riser: {riser}"


def _e(x: Any) -> str:
    return html.escape(str(x))


def render_html(d: dict[str, Any]) -> str:
    g, s = d["glance"], d["glance"]["split"]
    td = "padding:6px 8px;border-bottom:1px solid #e5e5e5;vertical-align:top;font-size:14px;"
    th = td + "text-align:left;background:#f3f6fb;font-weight:600;"
    tbl = "width:100%;border-collapse:collapse;margin:8px 0 18px 0;"
    parts = [
        '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta charset="utf-8"></head>',
        '<body style="margin:0;padding:0;background:#ffffff;">',
        '<div style="max-width:640px;margin:0 auto;padding:16px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'color:#1f2328;line-height:1.45;">',
        f'<h1 style="font-size:22px;margin:0 0 4px 0;">SkillPulse</h1>'
        f'<div style="color:#57606a;font-size:13px;margin-bottom:14px;">{_e(d["date"])} · India, {_e(s["preferred_city"])} first</div>',
        '<h2 style="font-size:17px;margin:18px 0 6px 0;">Today at a glance</h2>',
        f'<p style="margin:0 0 10px 0;font-size:14px;">{_e(g.get("paragraph", ""))}</p>',
        f'<table style="{tbl}">',
        f'<tr><td style="{td}">Postings (45-day snapshot)</td><td style="{td}"><b>{g["total_postings"]}</b></td></tr>',
        f'<tr><td style="{td}">New today / seen again</td><td style="{td}">{g["new_today"]} / {g["cached_today"]}</td></tr>',
        f'<tr><td style="{td}">{_e(s["preferred_city"])}</td><td style="{td}">{s["preferred_total"]} '
        f'(hybrid {s["preferred_hybrid"]}, remote {s["preferred_remote"]})</td></tr>',
        f'<tr><td style="{td}">India-remote</td><td style="{td}">{s["india_remote"]}</td></tr>',
        f'<tr><td style="{td}">Hybrid elsewhere in India</td><td style="{td}">{s["hybrid_elsewhere"]}</td></tr>',
        "</table>",
    ]
    parts.append('<div style="font-weight:600;font-size:14px;">Top rising skills (L1–L2, vs 7 days)</div>')
    if g["top_rising"]:
        parts.append(f'<table style="{tbl}"><tr><th style="{th}">Skill</th><th style="{th}">Share</th><th style="{th}">Δ 7d</th></tr>')
        for r in g["top_rising"]:
            parts.append(f'<tr><td style="{td}">{_e(r["skill"])}{" 🆕" if r.get("new") else ""}</td>'
                         f'<td style="{td}">{r["share"]:.0f}%</td><td style="{td};color:#1b7f3b;">↑ +{r["d7"]}pp</td></tr>')
        parts.append("</table>")
    else:
        parts.append('<p style="font-size:13px;color:#57606a;">No baseline yet. Trends appear after a few daily runs.</p>')

    for fam in d["families"]:
        if not fam["levels"]:
            continue
        parts.append(f'<h2 style="font-size:17px;margin:20px 0 6px 0;">{_e(fam["label"])}</h2>')
        parts.append(f'<table style="{tbl}"><tr><th style="{th}">Level</th><th style="{th}">Top 5 skills</th>'
                     f'<th style="{th}">Top 3 certifications</th></tr>')
        for lv in fam["levels"]:
            skills = "<br>".join(f'{_e(x["skill"])} <span style="color:#57606a;">{x["share"]:.0f}%'
                                 f'{" " + x["arrow7"] if x["arrow7"] in "↑↓" else ""}</span>' for x in lv["top_skills"][:5])
            certs = "<br>".join(_e(c["name"]) for c in lv["certifications"][:3]) or "–"
            parts.append(f'<tr><td style="{td}"><b>{_e(lv["level"])}</b><br><span style="color:#57606a;font-size:12px;">'
                         f'n={lv["n"]}</span></td><td style="{td}">{skills}</td><td style="{td}">{certs}</td></tr>')
        parts.append("</table>")
    h = d["health"]
    bad = [x["name"] for x in h.get("sources", []) if not x["ok"]]
    parts.append(f'<p style="font-size:12px;color:#57606a;margin-top:18px;">Full report attached (.docx). '
                 f'Cache hit {h.get("cache_hit_rate", 0):.0f}% · LLM ${h.get("cost_usd", 0):.3f}'
                 + (f' · <b style="color:#b42318;">failed sources: {_e(", ".join(bad))}</b>' if bad else "") + "</p>")
    parts.append("</div></body></html>")
    return "".join(parts)


def render_text(d: dict[str, Any]) -> str:
    g, s = d["glance"], d["glance"]["split"]
    L = [f"SkillPulse — {d['date']}", "", g.get("paragraph", ""), "",
         f"Postings: {g['total_postings']} (new today {g['new_today']}, seen again {g['cached_today']})",
         f"{s['preferred_city']}: {s['preferred_total']} | India-remote: {s['india_remote']} | Hybrid elsewhere: {s['hybrid_elsewhere']}",
         ""]
    if g["top_rising"]:
        L.append("Top rising (L1–L2): " + ", ".join(f"{r['skill']} +{r['d7']}pp" for r in g["top_rising"]))
        L.append("")
    for fam in d["families"]:
        for lv in fam["levels"]:
            L.append(f"{fam['label']} {lv['level']} (n={lv['n']}): "
                     + ", ".join(f"{x['skill']} {x['share']:.0f}%" for x in lv["top_skills"][:5]))
            if lv["certifications"]:
                L.append("   certs: " + "; ".join(c["name"] for c in lv["certifications"][:3]))
    L += ["", "Full report attached (.docx)."]
    return "\n".join(L)


def _base_message(sender: str, to: str, subject: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender  # must be the authenticated Zoho address
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    return msg


def build_report_message(d: dict[str, Any], sender: str, to: str, cfg: EmailConfig,
                         docx_path: Path | None, subject_prefix: str = "") -> EmailMessage:
    msg = _base_message(sender, to, subject_for(d, subject_prefix))
    msg.set_content(render_text(d))
    if cfg.html_body:
        msg.add_alternative(render_html(d), subtype="html")
    if cfg.attach_docx and docx_path and docx_path.exists():
        msg.add_attachment(docx_path.read_bytes(), maintype=DOCX_MIME[0], subtype=DOCX_MIME[1],
                           filename=f"SkillPulse-{d['date']}.docx")
    return msg


def build_failure_message(day: str, error: str, last_success: str | None, sender: str, to: str,
                          log_path: str = "logs/skillpulse.log") -> EmailMessage:
    msg = _base_message(sender, to, f"SkillPulse · {day} · RUN FAILED")
    body = (f"The SkillPulse run for {day} failed.\n\n"
            f"Last successful run: {last_success or 'never'}\n\n"
            f"Error:\n{error[-4000:]}\n\n"
            f"See {log_path} on the server for the full log.\n")
    msg.set_content(body)
    msg.add_alternative(
        '<div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:16px;">'
        f'<h2 style="color:#b42318;margin:0 0 8px 0;">SkillPulse run failed · {_e(day)}</h2>'
        f"<p>Last successful run: <b>{_e(last_success or 'never')}</b></p>"
        f'<pre style="white-space:pre-wrap;background:#f6f8fa;padding:10px;font-size:12px;">{_e(error[-4000:])}</pre>'
        f"<p style=\"color:#57606a;font-size:12px;\">Full log: {_e(log_path)}</p></div>",
        subtype="html",
    )
    return msg


def check_configured(secrets: Secrets) -> tuple[str, str, str]:
    if not (secrets.zoho_user and secrets.zoho_app_password and secrets.report_to):
        raise EmailNotConfigured("ZOHO_SMTP_USER, ZOHO_SMTP_APP_PASSWORD and REPORT_TO must be set in .env")
    return secrets.zoho_user, secrets.zoho_app_password, secrets.report_to


def send(msg: EmailMessage, cfg: EmailConfig, secrets: Secrets, attempts: int = 3) -> None:
    user, password, _ = check_configured(secrets)
    last: Exception | None = None
    for i in range(attempts):
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ctx, timeout=30) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
            log.info("email sent", extra={"to": msg["To"], "subject": msg["Subject"]})
            return
        except smtplib.SMTPAuthenticationError:
            raise  # retrying won't help; usually the login password was used instead of an app password
        except (smtplib.SMTPException, OSError) as e:
            last = e
            log.warning("email send failed, retrying", extra={"attempt": i + 1, "error": str(e)})
            time.sleep(2 * 2**i)
    assert last is not None
    raise last
