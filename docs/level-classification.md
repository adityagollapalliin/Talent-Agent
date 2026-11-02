# Level classification

## Purpose
Put every posting in a level bucket (L0, L1, L2, L3, L4+) so the report can compare like with like.
For example, "SDE 2" and "Software Engineer II" land together. Do it as cheaply as possible.

## Key files
- `memory/LEVEL_RUBRIC.md`: **the editable rule table, read at run time**. Change levels, year bands or title
  signals here, not in code.
- `skillpulse/classify/rubric.py`: parses the table; `normalise_title()`.
- `skillpulse/classify/years.py`: `parse_years()` regex parser.
- `skillpulse/classify/classifier.py`: `classify()`, `title_level()`, `years_to_level()`.
- `skillpulse/llm/*`: `classify_level()` on each `LLMClient` (fallback only).
- `tests/test_level_classifier.py`

## Data flow
```
posting (title, description, employment_type)
  ├─ employment_type == internship → L0 (high)
  ├─ parse_years(description) → (years_min, years_max) → years_to_level → (level, ambiguous)
  ├─ title_level(title) → highest level whose signal is a whole-word match
  ├─ both → agree: high | disagree: TITLE wins, confidence low, both kept in signals_used
  ├─ one  → medium (years-only ambiguous range → low)
  └─ none → LLM classify_level (given the rubric text; answer must be a rubric level) → else None (retried next run)
store.set_level(level, confidence, years_min, years_max, signals_used, ambiguous)
level ∉ report_levels → status skipped_level (counted, never sent to extraction)
```

## Decisions and why
- **Years parsing picks the first match near the word "experience"** (within 80 chars) in document order.
  Requirements normally come before "nice to have" lines, and the context check filters out
  "founded 25 years ago". Matches over 20 years are ignored.
- **Overlapping regex matches**: range > minimum > plus > plain, so "3-5 years" is never also read as "5 years".
- **Range spans two bands** → lower band + `ambiguous=True` (per spec). `2-5` is *not* ambiguous because 5 is L2's upper bound.
- **Title normalisation** makes `SDE-II`, `SDE2`, `SDE 2` and `SDE II` all equivalent, and expands `Sr.`/`Jr.`.
  Roman numerals i–v become digits, so the rubric only needs one spelling per signal.
- **Highest level wins among title signals**: "Senior Associate" is L3, not L1. Seniority words are the stronger
  signal in Indian consulting and analytics titles.
- **LLM returns None when unavailable** instead of guessing. The posting stays unclassified and is retried next run,
  so a budget stop never mislabels data.

## Known limitations
- "Lead" in the rubric matches people-manager "Team Lead" titles too. Those land in L3, not L4+.
- Years given only in words ("three years") are not parsed. That's rare in Indian JDs, and the LLM fallback covers it.
- Adzuna descriptions are truncated snippets, so the years requirement is sometimes cut off. The title signal or LLM then decides.

## TODOs
- Learn per-company title→level mappings (e.g. "Company X calls mid-level 'Engineer II'") from data and feed them back
  as extra signals. The notes are already written to `memory/MEMORY.md`.
