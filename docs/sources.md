# Sources

## Purpose
Find current postings in India: Hyderabad first, plus India-remote and hybrid roles, matching the configured titles.
Use only **legal, ToS-safe** sources. Every source returns normalised `RawPosting`
(`source, url, title, company, location, work_mode, employment_type, posted_date, description_text`).

**Never scrape LinkedIn, Naukri, Indeed, Glassdoor, Instahyre or Wellfound HTML.**
`sources/base.py: BANNED_DOMAINS` also drops any posting URL on those domains, e.g. ones a web search returns.

## Key files
- `skillpulse/sources/base.py`: `Source` Protocol (`name`, `fetch(ctx) -> list[RawPosting]`), `FetchContext`, `SourceSkipped`.
- `skillpulse/sources/adzuna.py`: Adzuna API (priority 1).
- `skillpulse/sources/ats.py`: Greenhouse / Lever / Ashby public job-board APIs (priority 2), read from `config/companies.yaml`.
- `skillpulse/sources/websearch.py`: LLM web search (priority 3, only when the API sources are thin).
- `skillpulse/sources/aggregator.py`: SerpApi Google Jobs (priority 4, off by default, paid).
- `skillpulse/sources/fixtures.py`: `tests/fixtures/postings_sample.json`, used by `--dry-run`.
- `skillpulse/sources/matching.py`: `TitleMatcher` (title → role family), `LocationFilter` (India / Hyderabad / keep rule).
- `skillpulse/sources/detect.py`: work mode (remote/hybrid/onsite/unknown) and employment type detection.
- `skillpulse/util/retry.py`: `request_json` (exponential backoff on 429/5xx/transport errors, honours `Retry-After`) and `RateLimiter`.

## Data flow
```
for source in [adzuna, ats_boards, web_search, aggregator]:      # config `sources:` toggles each
    try: raw = source.fetch(ctx)  except SourceSkipped → logged as skipped | Exception → logged as failed, run continues
    for p in raw: normalise_posting(p) → drop reason or keep → store.upsert_posting (dedupe)
```
`normalise_posting` (in `pipeline.py`):
1. Drop banned domains.
2. `TitleMatcher.match(title)`: the longest configured title contained as whole words (after `normalise_title`, so
   `SDE-II` == `SDE 2`) assigns `role_family`. If no title matches → `off_target_title` (e.g. "Senior Sales Executive"
   returned by a phrase search).
3. Fill `work_mode` / `employment_type` from text if the source didn't provide them.
4. `city` = preferred city if mentioned (Secunderabad counts as Hyderabad), else the first `region.india_locations` hit.
5. Must be in India (the location mentions an India location, or remote + "India" in the text; Adzuna `in` is assumed India).
6. **Keep rule:** Hyderabad (any mode) **or** remote (when `include_india_remote`) **or** hybrid anywhere in India.
   Onsite roles outside Hyderabad are dropped.

### Adzuna (`adzuna.py`)
- `GET https://api.adzuna.com/v1/api/jobs/{country_code}/search/1`, with `what_phrase=<title>`, `results_per_page` =
  `min(max_postings_per_title_per_run, sources.adzuna.results_per_page)`, `max_days_old=30`, `sort_by=date`.
- Two queries per configured title: `where=<preferred_city>`, and India-wide `what_or="remote hybrid"`.
- Credentials: `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`. Missing → `SourceSkipped`, not a failure.
- `contract_time` / `contract_type` → employment type. `created` → `posted_date`.
- Each query key is written to `search_log`. The same query within `cache.search_repeat_hours` is not re-run.

### Greenhouse / Lever / Ashby (`ats.py`)
- Companies come from `config/companies.yaml` (`greenhouse:` / `lever:` / `ashby:` lists of `{name, token}`).
  **The seeded tokens are unverified.** Run `uv run skillpulse check-boards` to see which respond and how many India
  + matching roles each has.
- Endpoints (public, unauthenticated job-board APIs):
  `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` ·
  `GET api.lever.co/v0/postings/{token}?mode=json` ·
  `GET api.ashbyhq.com/posting-api/job-board/{token}`.
- Full JDs. Greenhouse HTML is converted to text with `util/text.html_to_text`. Lever `workplaceType`/`commitment` and
  Ashby `workplaceType`/`isRemote`/`employmentType` are used when present, with text detection as the fallback.
- One request per board per run (`search_log` key `kind|token`). A cheap title + India pre-filter runs, then
  `max_postings_per_title_per_run` per matched title.
- A bad token logs a warning, and the source status shows `partial: N failed — …`. The source only fails if **every** board fails.

### LLM web search (`websearch.py`)
- Runs **only when** the earlier sources kept fewer than `sources.web_search.only_if_fewer_than` postings (default 40).
- One query per role family (≤ `max_queries_per_run`), using the configured provider's built-in tool
  (OpenAI `web_search` with `include=["web_search_call.action.sources"]`; Anthropic `web_search_20260209`), localised
  to India / Hyderabad.
- **Only results whose URL was actually returned by the search tool are kept** (compared after `normalise_url`).
  Banned domains and search/listing-page URLs are dropped, and URLs are never fetched.
- Uses the summary model, guarded by the cost cap. The description is the model's summary of the posting, so it's thinner than an ATS JD.

### SerpApi Google Jobs (`aggregator.py`), off by default
- `sources.aggregator.enabled: true` + `SERPAPI_API_KEY`. One query per title, located at the preferred city.
- Uses the first apply link that is not on a banned domain. A result with only banned links (or a Google share link) is dropped.
- JSearch is not implemented (`provider: jsearch` → skipped with a message).

## Decisions and why
- **The posting title, not the search term, decides the family.** Adzuna's phrase search also matches descriptions,
  so a search title alone would let sales roles in.
- **Hybrid outside Hyderabad is kept** because the spec asks the report to highlight remote + hybrid.
  Onsite elsewhere is noise for a Hyderabad-first report.
- **A failing source never fails the run.** Its error is stored in `runs.source_status` and shown in "Sources and run health".

## Known limitations
- **Adzuna descriptions are truncated snippets (~500 chars).** Years and skills past the cut-off are missed. ATS sources
  give full JDs, which is why they're priority 2. The `redirect_url` is an Adzuna landing URL and is not fetched (no scraping).
- All source fixtures (`tests/fixtures/{adzuna_search,greenhouse_jobs,lever_postings,ashby_board,serpapi_google_jobs}.json`)
  are hand-built in each API's documented response shape. They are not live captures, because the build sandbox could not
  reach those APIs. Re-record them from real responses when convenient.
- ~44 titles × 2 queries = ~88 Adzuna calls/day. Check this against your Adzuna plan's daily limit, and trim titles or
  turn off the remote query if needed.

## TODOs
- Paginate Adzuna (page 2+) for the broadest titles, if 25/title proves too few.
- JSearch (RapidAPI) as an alternative paid aggregator.
- More ATS types (SmartRecruiters, Workable, Workday). Many large Hyderabad employers use Workday, which has no public API.
