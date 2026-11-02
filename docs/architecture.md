# Architecture

## Purpose
A single-process daily batch job: discover postings → cache → classify → extract → aggregate → report → email.
No server and no queue. State lives in one SQLite file, and the code that touches it sits behind one interface.

## Pipeline (skillpulse/pipeline.py `run_daily`)
```
cli.py run ──▶ run_lock(data/skillpulse.lock) ──▶ run_daily(day = today in Asia/Kolkata)
  1 store.start_run
  2 collect(sources)                       sources/*  (each failure isolated → SourceStatus)
       normalise_posting: banned-domain / title→family / mode / type / city / India / keep rule
       store.upsert_posting                dedupe: normalised URL OR description SHA-256
  3 classify_pending                       classify/* (regex → title → LLM fallback), L0/L4+ → skipped_level
  4 extract_pending                        extract/* (cache by hash → cost guard → LLM → aliases)
  5 build_report                           report/aggregate (all maths) → save_daily_aggregates → prose (LLM or deterministic)
  6 write_report_files                     reports/DATE.md · .json · .docx
  7 store.finish_run(success, tokens, cost, source_status) · write memory/MEMORY.md
  8 email report                           deliver/email (Zoho SMTP_SSL)
  ✗ any exception → finish_run(failed, error) → failure email (last successful run date) → exit 1
```

## Components
| Package | Responsibility | Doc |
|---|---|---|
| `sources/` | Source plugins + matching/detection | [sources.md](sources.md) |
| `classify/` | Level rubric + classifier | [level-classification.md](level-classification.md) |
| `llm/` + `extract/` | Provider clients, cost tracker, extraction schema, aliases | [extraction.md](extraction.md) |
| `store/` | `Store` Protocol + SQLite | [memory-and-cache.md](memory-and-cache.md) |
| `report/` | Aggregation, prose, md/json/docx, MEMORY.md | [reporting.md](reporting.md) |
| `deliver/` | Email | [email-delivery.md](email-delivery.md) |
| `util/` | JSON logging, retry/backoff + rate limit, lock file, text helpers | this file |
| `deploy/` | setup script, systemd units, backup | [deployment.md](deployment.md) |

## Where state lives
| Path | What | Committed? |
|---|---|---|
| `data/skillpulse.db` | **Source of truth**: postings, extractions, runs, daily_aggregates, search_log | no |
| `data/backups/` | nightly DB snapshots (14 kept) | no |
| `data/dry-run/` | dry-run DB + MEMORY.md (fixtures only) | no |
| `reports/` | daily md/json/docx archive (`reports/dry-run/` for dry runs) | no |
| `logs/` | JSON-lines logs, rotated at midnight, 30 kept | no |
| `memory/MEMORY.md` | regenerated summary | yes (optional) |
| `memory/LEVEL_RUBRIC.md`, `memory/skill_aliases.yaml`, `config.yaml`, `config/companies.yaml` | human-edited rules/config | yes |
| `.env` | secrets (`chmod 600`) | **never** |

## Reliability features
- **Lock file** (`util/lock.py`): `fcntl.flock` on `data/skillpulse.lock`. A second run exits with code 2. The OS releases
  the lock if the process dies, so there are no stale locks.
- **Cost guard** (`llm/base.py CostTracker`): one tracker per run, shared by the classifier fallback, extraction, web search
  and prose. Each call is pre-checked: estimate + spent ≤ `max_cost_usd_per_run` (minus `summary_reserve_usd` for everything
  except the prose call). Actual cost is recorded from API usage. Postings deferred by the guard are retried next run.
- **Retries**: plain HTTP via `util/retry.request_json` (429/5xx/transport errors, exponential backoff 2-4-8 s with jitter,
  honours `Retry-After`, no retry on other 4xx). SDKs use their own `max_retries=4`. SMTP is retried 3× (not on auth errors).
- **Rate limits**: `RateLimiter(min_interval_s)` per source (from `sources.<name>.min_interval_s`).
- **Dry run** (`--dry-run`): `FixtureSource` + `FakeLLM`, separate `data/dry-run/` DB and `reports/dry-run/`, no email.
  Tests assert it makes zero HTTP and SMTP calls.
- **Logging**: JSON lines to stdout (captured by journald/cron) and to `logs/skillpulse.log` (daily rotation, 30 days).
  Extra context (source, model, tokens, cost) goes in as structured fields.

## Decisions and why
- **Batch, not a service**: the job runs once a day and finishes in minutes, so a timer + CLI is the least to operate.
- **SQLite behind a Protocol**: zero ops now. The later move to GitHub Actions + object storage only touches `store/`.
- **Python owns the numbers, the LLM owns the words**: reproducible reports, and nothing for the LLM to invent.

## Known limitations
- Single-threaded fetching. ~90 Adzuna + ~20 board calls with rate limiting take a few minutes, which is fine for a 06:30 run.

## TODOs
- Parallel fetches per source with a shared rate limiter, if the title list grows a lot.
