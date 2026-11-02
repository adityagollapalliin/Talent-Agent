# AGENTS.md — SkillPulse

SkillPulse is a Python 3.12+ service that runs once a day (systemd timer or cron) and:

1. finds current job postings in **India**, Hyderabad first, plus India-remote and hybrid roles,
   for the role families and titles in `config.yaml`;
2. classifies each posting's **level** (L0–L4+), using regex on years of experience, then title signals, then the LLM only as a last resort;
3. extracts **skills, technologies, certifications and "ideal candidate" traits** with an LLM (structured output → Pydantic);
4. **caches** everything in SQLite so no job description is searched or sent to the LLM twice;
5. builds a **daily trends report** per role family × level (numbers in Python, prose by the LLM);
6. **emails** it via Zoho SMTP: an HTML summary in the body and the full report as a `.docx` attachment.

## Read this first

| Doc | What it covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components, pipeline order, where state lives |
| [docs/sources.md](docs/sources.md) | Adzuna, Greenhouse/Lever/Ashby, LLM web search, SerpApi; the no-scraping rule |
| [docs/level-classification.md](docs/level-classification.md) | Regex → title → LLM classifier, `memory/LEVEL_RUBRIC.md` |
| [docs/memory-and-cache.md](docs/memory-and-cache.md) | SQLite schema, dedupe, TTL, `memory/MEMORY.md`, `Store` interface |
| [docs/extraction.md](docs/extraction.md) | Extraction schema, aliases, LLM clients, cost guard |
| [docs/reporting.md](docs/reporting.md) | Aggregation maths, baselines, md/json/docx outputs |
| [docs/email-delivery.md](docs/email-delivery.md) | Zoho SMTP, HTML body, failure emails, `send-test` |
| [docs/deployment.md](docs/deployment.md) | Ubuntu server, systemd timer, backups, migrating to GitHub Actions |
| [docs/DEVLOG.md](docs/DEVLOG.md) | Dated build log (newest first). Older months are in `docs/devlog/` |

## How to run

```bash
uv sync                                  # install (Python 3.12+)
cp .env.example .env && chmod 600 .env   # fill in keys
uv run skillpulse run --dry-run          # fixtures only: no network, no LLM, no email
uv run skillpulse run                    # the real daily run
uv run skillpulse report --date 2026-11-02 [--email]   # re-render (and optionally re-send) a day
uv run skillpulse send-test              # sample email to REPORT_TO, to check SMTP
uv run skillpulse cache stats            # what's cached, hit rates, spend
uv run skillpulse backup                 # snapshot the DB to data/backups/ (keeps 14)
uv run skillpulse config                 # validate + print config
uv run skillpulse check-boards           # probe config/companies.yaml board tokens (India / matching role counts)
uv run pytest                            # tests (recorded fixtures only, never live APIs)
```

## Where things live

```
skillpulse/
  cli.py         entry point (argparse)
  config.py      config.yaml + .env loading (Settings, Secrets, Paths)
  models.py      RawPosting, LevelResult, SourceStatus
  pipeline.py    the daily run: fetch → dedupe → classify → extract → aggregate → report → email
  sources/       Source plugins (adzuna, ats [greenhouse/lever/ashby], websearch, aggregator, fixtures)
                 + matching.py (title → role family, India/Hyderabad filter) + detect.py (work mode, employment type)
  classify/      years.py (regex), rubric.py (reads LEVEL_RUBRIC.md), classifier.py
  llm/           base.py (LLMClient, CostTracker), openai_client.py, anthropic_client.py, fake.py (dry run)
  extract/       schema.py (Pydantic), aliases.py, extractor.py
  store/         base.py (Store protocol), sqlite_store.py — the ONLY code that touches the DB
  report/        aggregate.py (all the maths), prose.py (LLM write-up), markdown.py, docx_builder.py, memory_md.py
  deliver/       email.py (Zoho SMTP_SSL, HTML + plain text, .docx attachment, failure email)
  util/          logging_setup.py, retry.py (backoff + rate limit), lock.py
config.yaml              behaviour (titles, levels, focus tech, models, budgets)
config/companies.yaml    ATS boards to poll (you curate)
memory/                  LEVEL_RUBRIC.md (editable rules), skill_aliases.yaml, MEMORY.md (regenerated)
data/                    skillpulse.db (+ backups/); dry runs use data/dry-run/
reports/                 YYYY-MM-DD.{md,json,docx}
logs/                    JSON-lines logs, rotated daily
deploy/                  setup.sh, systemd units, backup
tests/                   pytest + tests/fixtures (recorded API responses)
```

## Rules for agents working here

- **No hardcoded titles, levels or technologies.** They come from `config.yaml`, `memory/LEVEL_RUBRIC.md` and `memory/skill_aliases.yaml`.
- **Never scrape** LinkedIn, Naukri, Indeed, Glassdoor, Instahyre or Wellfound HTML. Only use APIs and public ATS job-board APIs.
- **Only `skillpulse/store/` touches storage.** It's the seam for the later move to GitHub Actions + object storage.
- **Python computes every number.** The LLM only writes prose from aggregates it is given.
- **Secrets only in `.env`** (never committed). `.env.example` lists them.
- Update the matching `docs/*.md` **in the same commit** as the code change, and add a `docs/DEVLOG.md` entry.
- **500-line rule:** no `.md` file may exceed 500 lines. When one gets close, split it (e.g. `docs/sources-adzuna.md`) and update the index above. Move old devlog months to `docs/devlog/DEVLOG-YYYY-MM.md`.
- Tests use recorded fixtures in `tests/fixtures/`, never live APIs.
