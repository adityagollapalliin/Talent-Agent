# Email delivery (Zoho Mail)

## Purpose
Deliver the report every morning to the owner's Zoho inbox: an HTML summary in the body, a plain-text fallback, and the
full `.docx` attached. **If the run fails, a short failure email is sent instead**, so the report never silently goes missing.

## Key files
- `skillpulse/deliver/email.py`: `build_report_message`, `build_failure_message`, `render_html`, `render_text`,
  `subject_for`, `send` (SMTP_SSL with retries), `check_configured`.
- `skillpulse/pipeline.py`: `run_daily` (success → report email; exception → failure email), `send_test`.
- `config.yaml` → `delivery.email` (`enabled`, `attach_docx`, `html_body`, `smtp_host`, `smtp_port`).
- `.env`: `ZOHO_SMTP_USER`, `ZOHO_SMTP_APP_PASSWORD`, `REPORT_TO`.

## Data flow
```
run_daily: report data + reports/DATE.docx
  → build_report_message(data, From=ZOHO_SMTP_USER, To=REPORT_TO)
       Subject: "SkillPulse · {date} · {n} postings · Top riser: {skill}"   ({n} = snapshot size; "—" if no riser yet)
       text/plain (fallback)  +  text/html (mobile-friendly)  +  SkillPulse-{date}.docx
  → send(): smtplib.SMTP_SSL(smtp.zoho.in, 465) → login(user, app password) → send_message
on any exception in run_daily:
  → runs.status = failed, error stored → build_failure_message(day, error+traceback, last successful run date)
  → "SkillPulse · {date} · RUN FAILED"
```

## Setup (one time)
1. In Zoho Mail: **Settings → Security → App Passwords → Generate**, and name it "SkillPulse".
   Use that app-specific password, **not** the login password (with 2FA on, the login password is refused).
2. `.env`: `ZOHO_SMTP_USER=<your full Zoho address>`, `ZOHO_SMTP_APP_PASSWORD=<app password>`, `REPORT_TO=<inbox>`.
3. `uv run skillpulse send-test` builds a sample report from fixtures in a temp dir (no LLM, real DB untouched)
   and sends it with a `[TEST] ` subject prefix.
4. For an account on another Zoho data centre, change `delivery.email.smtp_host`
   (`smtp.zoho.com` for .com, `smtp.zoho.eu` for .eu). The `.in` account uses `smtp.zoho.in`.

## HTML body
Single 640px column, inline styles only (email clients strip `<style>`), a `viewport` meta tag, and 100%-width tables.
Contents: "Today at a glance" (paragraph, counts, location split, top risers), then **one compact table per role family**
(rows = levels; columns = top 5 skills with share and ↑/↓, top 3 certifications). The footer shows the cache hit rate, the LLM cost,
and any failed sources in red.

## Decisions and why
- **From = the authenticated address**: Zoho rejects mail whose From doesn't match the login (relaying is not allowed).
- **SMTP_SSL on 465** (implicit TLS) rather than STARTTLS on 587, per the spec. It's one fewer negotiation step to fail.
- **Auth errors are not retried**: they're always configuration problems (usually the login password used instead of an app password).
  Network errors are retried 3× with backoff.
- **Delivery failure after a good run marks the run failed** (and tries the failure email). The next run's failure email
  then reports the correct "last successful run".
- **Dry runs never email.** `send-test` is the way to test delivery.

## Known limitations
- If SMTP itself is down, the failure email can't be sent either. The systemd unit's non-zero exit is the last line of
  defence (see deployment.md: `OnFailure=` hook or journal monitoring).
- The HTML is tested for structure, not rendered in real clients. Check it on your phone after `send-test`.

## TODOs
- An optional second channel (e.g. Telegram bot) for failure alerts that doesn't depend on SMTP.
