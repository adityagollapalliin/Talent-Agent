# Level rubric

The classifier (`skillpulse/classify/`) **reads this file at run time**. Edit the table to change behaviour, with no code change.
The parser only reads the table: keep its columns, add or remove rows, and keep signals comma-separated.

| Level | Years min | Years max | Title signals | Notes |
|---|---|---|---|---|
| L0 | 0 | 0 | Intern, Internship, Trainee, Apprentice, Student, Summer Analyst | Student / internship. Stored and counted, not detailed. |
| L1 | 0 | 2 | Junior, Jr, Associate, Graduate, Fresher, Entry Level, New Grad, SDE 1, Engineer 1, Developer 1, Analyst 1, Engineer I, Analyst I | 0–2 years |
| L2 | 2 | 5 | SDE 2, Engineer 2, Developer 2, Analyst 2, Engineer II, Analyst II, Mid Level, Intermediate | 2–5 years; plain "Software Engineer" with 2–5 yrs lands here via years |
| L3 | 5 | 8 | Senior, Sr, SDE 3, Engineer 3, Engineer III, Analyst 3, Lead, Tech Lead | 5–8 years; Lead as an individual contributor |
| L4+ | 8 | | Staff, Principal, Architect, Manager, Director, Head, Distinguished, Fellow, VP | 8+ years. Stored and counted, not detailed. |

## Rules (implemented in `classify/classifier.py`)

1. **Regex pass first (no LLM cost).** Parse `3+ years`, `2-5 yrs`, `minimum 4 years`, `5 to 7 years`, `0-1 year`,
   `freshers`. Map `years_min` to the band with `min <= years_min < max` (L0 is title/internship only).
   If `years_max` goes past that band's max, the range spans two levels: keep the lower band and mark it **ambiguous**.
2. **Title signals confirm or override.** Matching is case-insensitive and on whole words. `SDE-1`, `SDE1`, `SDE I`
   and `SDE 1` are equivalent (roman numerals → digits, `Sr.` → `Senior`, `Jr.` → `Junior`).
   If several levels match, the **highest** wins (e.g. "Senior Associate" → L3).
   If years and title conflict, use the **title**, set `confidence: low`, and keep both in `signals_used`.
3. **LLM only if both fail.** It is given this rubric and must answer one of the levels above.
4. Internship employment type → L0 regardless.
5. Confidence: years + title agree → high. Only one of them (unambiguous) → medium. Ambiguous range only, or a conflict → low.
