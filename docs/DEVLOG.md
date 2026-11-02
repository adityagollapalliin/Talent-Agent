# DEVLOG

Newest at the top. One entry per meaningful step. Move older months to `docs/devlog/DEVLOG-YYYY-MM.md`.

## 2026-11-02 — Step 4: Adzuna source end to end

- **Did:** `Source` Protocol + `FetchContext`, Adzuna source (Hyderabad + India remote/hybrid queries, search cache),
  backoff/rate-limit HTTP helper, work-mode/employment detection, title→family matcher, India/Hyderabad filter,
  `pipeline.collect()` + `classify_pending()`, fixtures (`adzuna_search.json`, 14-posting `postings_sample.json`), tests
  including an end-to-end Adzuna run on `httpx.MockTransport`.
- **Decisions:** the posting's own title decides the family (phrase search leaks off-target roles). Onsite roles outside
  Hyderabad are dropped; hybrid anywhere in India is kept.
- **Gotcha:** fixture titles "Data Science Intern" / "Staff Engineer" matched no configured title and were dropped as off-target.
  That's correct behaviour, so I renamed the fixtures to titles that are in config.
- **Next:** LLM client (OpenAI first) + structured extraction.

## 2026-11-02 — Step 3: level classifier + rubric + tests

- **Did:** `memory/LEVEL_RUBRIC.md` (machine-readable table + rules), rubric parser, years regex parser,
  title-signal matcher, `classify()` with the LLM as a pluggable last resort. 45 classifier tests.
- **Decisions:** the highest title signal wins; title beats years on conflict (low confidence); an unavailable LLM → None,
  so the posting is retried rather than guessed. Roman numerals and Sr/Jr are normalised so the rubric needs one spelling per signal.
- **Considered and rejected:** "largest years_min wins" across all matches. It over-weights "nice to have 5+ years"
  lines, so the parser uses "first match with experience context".
- **Next:** Adzuna source end to end.

## 2026-11-02 — Step 2: SQLite store + cache with dedupe

- **Did:** `Store` Protocol, `SqliteStore` (postings, extractions, runs, daily_aggregates, search_log), URL normalisation
  and description hashing, backup with 14-copy rotation, 9 tests.
- **Decisions:** dedupe on URL **or** description hash. Hash dedupe only applies to descriptions over 200 chars, to avoid
  merging distinct jobs that share a generic snippet. TTL is keyed on `first_seen`. A `search_log` table stops repeat queries.
- **Next:** level classifier + rubric.

## 2026-11-02 — Step 1: skeleton, config loading, AGENTS.md, docs stubs

- **Did:** `uv` project (`pyproject.toml`, Python ≥3.12), package layout, `config.yaml` (confirmed by owner),
  `skillpulse/config.py` (Pydantic `Settings`, `Secrets` from `.env`, `Paths`), JSON logging util,
  `.env.example`, `.gitignore`, `AGENTS.md`, stubs for every `docs/*.md`.
- **Decisions:**
  - Model names: OpenAI `gpt-6-luna` (extraction) / `gpt-6.1-sol` (summary). They come from the `ChatModel` literal in
    `openai` SDK 3.42.1, because developers.openai.com is not reachable from the build sandbox. Prices are
    from third-party pricing pages, **so verify them**. Anthropic: `claude-haiku-5-5` / `claude-opus-5-5` (from the Anthropic docs).
  - Pricing lives in `config.yaml` so the cost guard needs no code change when prices move.
  - Added `region.india_locations` (city list used to filter global ATS boards), `summary_reserve_usd`,
    `sources`, `cache`, `report` blocks. All of them are config, not code.
  - Dry runs write to `data/dry-run/` and `reports/dry-run/` so fixtures never pollute real history.
- **Next:** SQLite store + dedupe.
