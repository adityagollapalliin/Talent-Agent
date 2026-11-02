"""Per-posting skill extraction: cache check → cost guard → LLM structured output → alias normalisation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from skillpulse.extract.aliases import Aliases
from skillpulse.extract.schema import SKILL_LIST_FIELDS, JobExtraction
from skillpulse.llm.base import BudgetExceeded, LLMClient
from skillpulse.store.base import Store

log = logging.getLogger("skillpulse.extract")
MAX_DESC_CHARS = 16000  # bounds per-call cost; real JDs are almost always shorter
MAX_OUTPUT_TOKENS = 1500


def build_instructions(focus: list[str], canonical_names: list[str]) -> str:
    return (
        "You extract structured hiring requirements from ONE job posting. Rules:\n"
        "- Only include items the posting actually states. Never infer or invent skills.\n"
        "- must_have_skills = required/mandatory; nice_to_have_skills = preferred/plus/bonus/good to have.\n"
        "  Put each concrete skill/technology in exactly one of these two lists, and ALSO in its category list "
        "(languages, frameworks, cloud, ai_platforms, data_platforms, tools).\n"
        "- Use these canonical names when they apply (otherwise the name as written, Title Case): "
        + ", ".join(canonical_names) + ".\n"
        "- certifications: full official certification names.\n"
        "- soft_skills_and_expectations: short phrases (2-5 words) describing the ideal candidate.\n"
        "- years_min/years_max: the stated experience requirement, else null.\n"
        "- work_mode / employment_type: as stated, else 'unknown'. city: the job's city, else null.\n"
        "- matches_focus_technologies: the subset of THIS list the posting asks for: " + "; ".join(focus) + "."
    )


def posting_text(row: dict[str, Any]) -> str:
    desc = (row.get("description_text") or "")[:MAX_DESC_CHARS]
    return (f"Title: {row['title']}\nCompany: {row.get('company') or ''}\nLocation: {row.get('location') or ''}\n"
            f"Work mode (from source): {row.get('work_mode')}\n\nDescription:\n{desc}")


def postprocess(ext: JobExtraction, aliases: Aliases) -> dict[str, Any]:
    d = ext.model_dump()
    for f in (*SKILL_LIST_FIELDS, "certifications"):
        d[f] = aliases.normalise_list(d[f])
    d["soft_skills_and_expectations"] = [s.strip().rstrip(".").lower() for s in d["soft_skills_and_expectations"] if s.strip()]
    # Focus matches are recomputed deterministically; the LLM's list is only trusted if it names a focus item.
    llm_focus = {aliases.canonical(x) for x in d["matches_focus_technologies"]}
    every = [x for f in SKILL_LIST_FIELDS for x in d[f]]
    py_hits = set(aliases.focus_hits(every))
    d["matches_focus_technologies"] = [f for f in aliases.focus if f in py_hits or f in llm_focus]
    return d


@dataclass
class ExtractStats:
    extracted: int = 0
    cache_hits: int = 0
    failed: int = 0
    skipped_budget: int = 0


def extract_pending(store: Store, llm: LLMClient, aliases: Aliases, levels: list[str],
                    reserve_usd: float) -> ExtractStats:
    st = ExtractStats()
    instructions = build_instructions(aliases.focus, aliases.all_names())
    budget_hit = False
    for row in store.postings_needing_extraction(levels):
        h = row["desc_hash"]
        cached = store.get_extraction_by_hash(h)
        if cached:  # same JD text under another posting id: reuse, no LLM call
            store.save_extraction(row["id"], h, cached["provider"], cached["model"], cached["data"], 0, 0, 0.0)
            st.cache_hits += 1
            continue
        if budget_hit:
            store.set_status(row["id"], "skipped_budget")
            st.skipped_budget += 1
            continue
        text = posting_text(row)
        try:
            llm.guard("extraction", len(text) + len(instructions), MAX_OUTPUT_TOKENS, reserve_usd)
            ext = llm.extract_structured(text, JobExtraction, instructions=instructions,
                                         max_output_tokens=MAX_OUTPUT_TOKENS)
        except BudgetExceeded as e:
            log.warning("cost guard: stopping extraction", extra={"reason": str(e)})
            budget_hit = True
            store.set_status(row["id"], "skipped_budget")
            st.skipped_budget += 1
            continue
        except Exception:  # noqa: BLE001 — one bad posting must not stop the run
            log.exception("extraction failed", extra={"posting_id": row["id"]})
            store.set_status(row["id"], "extract_failed")
            st.failed += 1
            continue
        call = llm.last_call
        store.save_extraction(row["id"], h, llm.provider,
                              call.model if call else llm.extraction_model, postprocess(ext, aliases),
                              call.input_tokens if call else 0, call.output_tokens if call else 0,
                              call.cost_usd if call else 0.0)
        st.extracted += 1
    return st
