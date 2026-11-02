# Extraction (and the LLM layer)

## Purpose
Turn each cached, in-scope posting (level in `report_levels`) into a validated `JobExtraction`: skills, technologies,
certifications and ideal-candidate traits, using canonical names. Each posting is sent to the LLM at most once, within a hard per-run budget.

## Key files
- `skillpulse/extract/schema.py`: `JobExtraction` (Pydantic). All fields are required (nullable where noted), so the same
  model is a valid **OpenAI strict JSON schema** and **Anthropic structured-output schema** (checked offline with both SDKs' converters).
- `skillpulse/extract/extractor.py`: `extract_pending()` (cache → guard → LLM → postprocess → store), prompt builder.
- `skillpulse/extract/aliases.py` + `memory/skill_aliases.yaml`: alias → canonical names, plus focus-tech groups.
- `skillpulse/llm/base.py`: `LLMClient` ABC (`extract_structured`, `summarise`, `web_search`, `classify_level`),
  `CostTracker`, `BudgetExceeded`, `LLMRefusal`.
- `skillpulse/llm/openai_client.py`: Responses API, `client.responses.parse(text_format=Model)` → `output_parsed`.
- `skillpulse/llm/anthropic_client.py`: Messages API, `client.messages.parse(output_format=Model)` → `parsed_output`.
- `skillpulse/llm/fake.py`: `FakeLLM`, a deterministic keyword-scan extractor for `--dry-run` and tests. Zero cost.
- `skillpulse/llm/factory.py`: builds the client named by `llm.provider`.

## Data flow
```
store.postings_needing_extraction(report_levels)        # level in L1–L3, no extraction yet; Hyderabad first
  ├─ same description hash already extracted? → copy it (cache hit, $0)
  ├─ llm.guard(): estimated cost (chars/3.5 input + max output tokens) must fit in
  │     max_cost_usd_per_run − summary_reserve_usd, else BudgetExceeded → this and every remaining posting → skipped_budget
  ├─ llm.extract_structured(posting_text, JobExtraction)   # extraction_model
  └─ postprocess: aliases on every list, soft skills lower-cased,
       matches_focus_technologies RECOMPUTED in Python (alias + focus_groups); the LLM's list is only kept for real focus names
store.save_extraction(... tokens, cost) → status extracted
```
Postings marked `skipped_budget` or `extract_failed` are retried on the next run.

## Models (config.yaml → `llm`)
| Provider | extraction_model | summary_model | Source of the name |
|---|---|---|---|
| openai | `gpt-6-luna` | `gpt-6.1-sol` | `ChatModel` literal in openai SDK 3.42.1 (OpenAI's docs site was unreachable from the build sandbox) |
| anthropic | `claude-haiku-5-5` | `claude-opus-5-5` | Anthropic model docs |

Prices per 1M tokens live in `config.yaml` (`llm.<provider>.pricing`). **Check the OpenAI prices against OpenAI's pricing page**;
they come from third-party listings. An unknown model is priced pessimistically ($10/$50) and a warning is logged.

## Decisions and why
- **Python recomputes focus matches.** The report's focus table must be reproducible, and must not depend on whether
  the LLM remembered to fill a field.
- **Category lists + must/nice lists**: the prompt asks for each item in both. Aggregation uses must ∪ nice for "skills"
  and the category lists for per-category views.
- **Alias map includes certifications**, so "AI-102" and "Azure AI Engineer Associate" count as one.
- **Description capped at 16k chars** to bound cost on pathological postings. Real JDs are shorter.
- **OpenAI SDK retries** (`max_retries=4`) handle 429/5xx with backoff. Our own `request_json` handles plain HTTP sources.

## Known limitations
- Snippet-only sources (Adzuna) produce thinner extractions than full ATS JDs.
- Cost estimation is approximate (chars/3.5). The guard compares estimate + spent against the cap before each call, and
  actual cost is recorded from the API's usage numbers.
- `FakeLLM` is a crude keyword scanner. It exists so dry runs exercise the whole pipeline, not to produce good data.

## TODOs
- Batch APIs (OpenAI Batch / Anthropic Message Batches) at ~50% cost, if daily volume grows beyond a few hundred postings.
- Prompt caching of the static instructions prefix (both providers), once volume makes it worth it.
