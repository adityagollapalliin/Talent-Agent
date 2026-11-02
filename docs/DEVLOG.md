# DEVLOG

Newest at the top. One entry per meaningful step. Move older months to `docs/devlog/DEVLOG-YYYY-MM.md`.

## 2026-11-02 — Step 9: cost guard, dry-run, lock file, logging

- **Did:** `fcntl` lock file (`data/skillpulse.lock`, exit code 2 if held). Tests that prove the guarantees: the dry run
  makes zero HTTP/SMTP calls; a priced run with a tiny budget defers extraction, keeps spend ≤ cap and still reports;
  backoff retries 5xx but not 404; the rate limiter spaces calls; JSON logs carry extra fields and reach `logs/`.
  Wrote `docs/architecture.md`.
- **Gotcha:** the guard estimate includes the full `max_output_tokens` allowance, so very small caps block even the first
  call. That's intended (pessimistic), and noted in extraction.md.
- **Next:** deploy scripts, systemd timer, backups, deployment docs.

## 2026-11-02 — Step 8: Greenhouse/Lever/Ashby + web search sources, Anthropic client

- **Did:** `sources/ats.py` (three public board APIs, per-board failure isolation reported as "partial"),
  `sources/websearch.py` (thin-results trigger, URL must be one the search tool returned), `sources/aggregator.py`
  (SerpApi, off by default), `llm/anthropic_client.py` (`messages.parse` for extraction at effort low; beta Messages
  with `fallbacks="default"` for summary/web search; `web_search_20260209`; `pause_turn` loop; refusal → `LLMRefusal`),
  `config/companies.yaml` seed + `skillpulse check-boards`. Fixtures for every API shape. 11 new tests.
- **Decisions:** Anthropic web search asks for fenced JSON validated by Pydantic instead of `output_config.format`,
  because citations from search results and structured outputs don't combine. Cost is priced on the model that actually
  served (a fallback can change it).
- **Unverified:** the board tokens in `companies.yaml`, because the sandbox can't reach the ATS APIs. Use `check-boards`.
- **Next:** cost guard, dry-run, lock file, logging (hardening + docs).

## 2026-11-02 — Step 7: .docx builder + Zoho email (send-test, failure emails)

- **Did:** `report/docx_builder.py` (title page, Heading 1/2/3 structure, real "Light Grid Accent 1" Word tables, green ↑ /
  red ↓, clickable sample links via a raw `w:hyperlink`); `deliver/email.py` (multipart: plain + mobile HTML + .docx, Zoho
  `SMTP_SSL` on `smtp.zoho.in:465`, retry, failure email); `pipeline.run_daily` (full orchestration with failure path) and
  `send_test`; full CLI (`run`, `report --date`, `send-test`, `cache stats`, `backup`, `config`).
- **Verified:** `skillpulse run --dry-run` produces md/json/docx from fixtures. Tests cover the docx structure, MIME
  parts, the SMTP host/port/login (fake `SMTP_SSL`) and a forced mid-run crash that sends "RUN FAILED" with the last success date.
- **Fixes:** no-baseline cells showed "– –", now a single "–". The fake LLM returns empty prose, so dry runs show the
  deterministic numbers-based paragraphs.
- **Next:** Greenhouse/Lever/Ashby + web search sources, Anthropic client.

## 2026-11-02 — Step 6: aggregation + markdown report

- **Did:** `report/aggregate.py` (buckets, shares, 7/30-day baselines with a bucket-size sentinel, arrows, risers,
  focus table, location split, market certifications), `report/prose.py` (one structured summary call + deterministic
  fallback), `report/markdown.py`, `report/memory_md.py`, pipeline `build_report` / `write_report_files` / `write_memory`.
  Role families get an optional `label` in config.
- **Decisions:** absent items count as 0% on days the bucket existed (the sentinel row). Otherwise a skill that disappears
  would never show ↓. Risers use the pooled L1+L2 bucket for "at a glance".
- **Gotcha:** two test failures came from the test helper, which reused descriptions across seeds. The store merged them
  by description hash, which is the correct behaviour. I fixed the helper, not the code.
- **Next:** .docx builder + Zoho email (send-test, failure email).

## 2026-11-02 — Step 5: LLM client (OpenAI) + structured extraction

- **Did:** `LLMClient` ABC with `CostTracker` and budget guard; `OpenAIClient` (Responses API `responses.parse` with a
  Pydantic `text_format`; web search via the `web_search` tool + `include=["web_search_call.action.sources"]`);
  `JobExtraction` schema; `skill_aliases.yaml` + `Aliases` (canonical names + focus groups); `extract_pending()`;
  `FakeLLM` for dry runs; provider factory. Tests use a stand-in `responses` object, never the live API.
- **Verified offline:** `JobExtraction`/`LevelAnswer` convert cleanly with `openai.lib._pydantic.to_strict_json_schema`
  and Anthropic's `transform_schema`.
- **Decisions:** focus-tech matches are recomputed in Python; the extraction cache is also keyed by description hash
  (a short JD reposted under a new URL is never re-sent); budget stops mark postings `skipped_budget` for retry.
- **Next:** aggregation + markdown report.

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
