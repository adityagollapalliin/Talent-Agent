# Reporting

## Purpose
Turn the cached snapshot into a daily trends report per role family × level. **Python computes every number.**
The LLM only writes prose (the glance paragraph and the ideal-candidate paragraphs) from aggregates it is given.

## Key files
- `skillpulse/report/aggregate.py`: buckets, counts, shares, 7/30-day baselines, arrows, risers, focus table,
  location split, certifications. `build_report_data()` returns the report dict (also the `.json` archive).
- `skillpulse/report/prose.py`: `write_prose()`, one structured `summary_model` call (`ReportProse`), with a
  deterministic fallback.
- `skillpulse/report/markdown.py`: `render_markdown()` → `reports/YYYY-MM-DD.md`.
- `skillpulse/report/docx_builder.py`: `build_docx()` → `reports/YYYY-MM-DD.docx` (python-docx, real Word tables).
- `skillpulse/report/memory_md.py`: `render_memory()` → `memory/MEMORY.md`.
- `skillpulse/pipeline.py`: `build_report()`, `write_report_files()`, `write_memory()`.

## Data flow
```
store.snapshot(day, ttl_days)               # postings first seen in the last 45 days
  → build_buckets: (family, level) for level ∈ report_levels with an extraction; plus ("__all__", "L1+L2")
  → aggregate_rows → store.save_daily_aggregates(day)   # becomes tomorrow's baseline
  → build_report_data (uses Baselines over stored aggregates for days [day-7, day-1] and [day-30, day-1])
  → write_prose (LLM or deterministic) → render markdown / json / docx
```

## The maths
- **Share** = postings in the bucket whose must ∪ nice list contains the item ÷ extracted postings in the bucket × 100.
  Each posting counts an item once. "nice" excludes items the same posting already lists as must-have.
- **Bucket-size sentinel**: each bucket stores a `("_n", "_n")` row, so the baseline knows which days the bucket existed.
  An item absent on such a day counts as **0%** that day, not as missing.
- **Baseline(7d)** = mean of the item's stored daily share over days in `[day-7, day-1]` where the bucket existed.
  The same over 30 days for Baseline(30d). No such days → `–`.
- **Δ** = today's share − baseline, in percentage points. **Arrow**: ↑ if Δ > `report.trend_epsilon_pp` (1.0),
  ↓ if Δ < −ε, = otherwise.
- **Rising** = Δ7 > `report.rising_threshold_pp` (5.0). It is **new** if the 7-day baseline share was 0.
  The at-a-glance "top 5 rising for L1–L2" uses the pooled `("__all__", "L1+L2")` bucket.
- **Focus table**: per focus tech, % of postings where it is must-have / nice-to-have (via `Aliases.focus_hits`, so
  "Pinecone" counts toward "Vector databases"), and "any mention" (`matches_focus_technologies`).
- **Location split** (a partition of the snapshot): preferred city (with hybrid/remote counts) · India-remote outside
  it · hybrid elsewhere in India.
- **Levels**: L0 and L4+ appear only in the counts. L1/L2 get the full section. L3 gets top 10 skills, certifications
  and the focus table.

## Report sections
1. Today at a glance: paragraph, snapshot size (new today / seen again / analysed), location split, level counts, top 5 risers.
2. Per role family: L1, L2 in full (top 15 with 7d/30d Δ and arrows, rising, certifications, focus table,
   ideal candidate, 3 sample links with Hyderabad first), then the shorter L3 section.
3. Certifications across the market, mapped to levels.
4. Sources and run health: per-source status, cache hit rate, tokens, cost vs budget, extraction counts.

## Decisions and why
- **The snapshot is a rolling TTL window, not "fetched today".** Daily fetches are small and noisy. A 45-day window
  gives stable shares, and the deltas then show genuine drift.
- **One prose call for the whole report**, with structured output keyed by bucket. That's cheaper than one per bucket,
  and a missing or garbled bucket just keeps its deterministic paragraph.
- **The prose prompt receives only aggregated lists** (skill names with %), never JDs. That limits the room to invent skills.

## Known limitations
- Shares move slowly because of the 45-day window. A sudden spike shows as a modest Δ7.
- The "don't invent skills" rule is enforced by the prompt and the input shape, not by a post-check on the prose.
- Re-running `report --date` for a past day re-aggregates from the current DB. Postings first seen after that day are
  excluded by the snapshot's date bound, but extractions added later for older postings are included.

## TODOs
- A post-check that flags capitalised tokens in prose that aren't in the bucket's skill list.
