# Memory and cache

## Purpose
Never search for, or send to the LLM, the same job description twice. Keep a durable history for
trend baselines. Give the next agent or human a short, readable summary of what the system has learned.

## Key files
- `skillpulse/store/base.py`: the `Store` Protocol. **All state access goes through it.**
- `skillpulse/store/sqlite_store.py`: `SqliteStore`, the v1 implementation (`data/skillpulse.db`).
- `skillpulse/util/text.py`: `normalise_url`, `description_hash` (the two dedupe keys).
- `skillpulse/report/memory_md.py`: regenerates `memory/MEMORY.md` at the end of each run.
- `memory/MEMORY.md`: generated summary (<300 lines, never raw JDs).

## Data flow
1. Before calling a source, the pipeline checks `search_done_recently(source, query_key, cache.search_repeat_hours)`.
   If the same query ran within the window (default 20 h), it is skipped. A re-run on the same day costs no API calls.
2. Every fetched `RawPosting` → `upsert_posting(p, seen_on)`. Dedupe is by **normalised URL OR SHA-256 of the
   whitespace-normalised, case-folded description**. Hash dedupe only applies when the stored description
   is longer than 200 chars, so two different jobs with the same tiny snippet ("Apply now") don't merge.
   A hit bumps `last_seen` and `times_seen` and returns `is_new=False`.
3. The classifier runs only on rows with `level IS NULL`. Extraction runs only on rows with no `extractions` row and
   a level in `report_levels`. This is the cache check before any LLM call.
4. `snapshot(as_of, ttl_days)` returns postings with `first_seen` in the last `ttl_days` (default 45) up to `as_of`.
   Expired postings stay in the DB (history, baselines) but drop out of today's snapshot.
5. `save_daily_aggregates(day, rows)` stores per family × level × category shares. These are the 7- and 30-day baselines.

## Schema (SQLite)
| Table | Key | Notes |
|---|---|---|
| `postings` | `id`; `url_norm` UNIQUE; index on `desc_hash` | level fields, `status`, `first_seen`/`last_seen`/`times_seen` |
| `extractions` | `posting_id` | `data_json` (validated extraction), tokens, cost, provider/model |
| `runs` | `id` | per-run tokens, cost, counts, `source_status` JSON, `status` success/failed, `error` |
| `daily_aggregates` | (`day`,`family`,`level`,`category`,`item`) | `count`, `n_postings`, `share` (0–100) |
| `search_log` | (`source`,`query_key`) | last time a query ran, so it isn't repeated |

`postings.status` values: `new` → `classified` → `extracted`, or `skipped_level` (L0/L4+), `skipped_employment`
(internship/contract), `skipped_budget` (cost guard hit; retried next run), `extract_failed` (retried next run).

## Decisions and why
- **SQLite + WAL**: one file, zero ops, easy to back up (`Store.backup` uses the online backup API, keeps 14).
- **Two dedupe keys**: the same job is often cross-posted (Adzuna redirect URL vs the ATS URL), and URL alone misses that.
- **Dates are local-calendar strings (`YYYY-MM-DD`, Asia/Kolkata)** passed in by the pipeline. The store never calls
  `date.today()` itself, so tests and back-fills are deterministic.
- **Dry runs use `data/dry-run/skillpulse.db`** so fixtures never mix with real history.
- **The `Store` Protocol is the migration seam.** For GitHub Actions: download the DB from object storage at job start,
  run, then upload it. Or implement `Store` against a hosted DB. Nothing outside `skillpulse/store/` changes.

## Known limitations
- Hash dedupe needs identical text (after whitespace/case folding). Near-duplicates with small edits are separate rows.
- `first_seen` is used for TTL rather than the source's `posted_date`, which many sources omit or report inconsistently.

## TODOs
- Fuzzy near-duplicate detection (MinHash over shingles) if cross-posting noise turns out to matter.
- An object-storage `Store` wrapper for GitHub Actions (see deployment.md).
