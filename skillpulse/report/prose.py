"""LLM-written prose for the report, from aggregated numbers only. Falls back to deterministic text."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

from skillpulse.llm.base import LLMClient

log = logging.getLogger("skillpulse.report.prose")


class IdealCandidate(BaseModel):
    bucket: str
    paragraph: str


class ReportProse(BaseModel):
    glance_paragraph: str
    ideal_candidates: list[IdealCandidate]


INSTRUCTIONS = (
    "You write short, plain-English commentary for a daily job-market skills report (India, Hyderabad first).\n"
    "Use ONLY the numbers and items in the JSON you are given. Do not mention any skill, technology, certification or "
    "trait that is not listed in the input, and do not invent numbers.\n"
    "glance_paragraph: 2-3 sentences summarising the day (volume, location split, notable risers if any).\n"
    "ideal_candidates: exactly one entry per input bucket (copy `bucket` verbatim), a 2-3 sentence 'ideal candidate' "
    "paragraph built from top_expectations and top_skills."
)


def _bucket_key(family: str, level: str) -> str:
    return f"{family}|{level}"


def prose_payload(data: dict[str, Any]) -> dict[str, Any]:
    g = data["glance"]
    buckets = []
    for fam in data["families"]:
        for lv in fam["levels"]:
            if not lv.get("primary"):
                continue
            buckets.append({
                "bucket": _bucket_key(fam["family"], lv["level"]),
                "role_family": fam["label"], "level": lv["level"], "postings": lv["n"],
                "top_skills": [f"{s['skill']} ({s['share']:.0f}%)" for s in lv["top_skills"][:8]],
                "top_expectations": [f"{e['trait']} ({e['share']:.0f}%)" for e in lv.get("expectations", [])[:6]],
                "certifications": [c["name"] for c in lv["certifications"][:3]],
            })
    return {
        "glance": {"total_postings": g["total_postings"], "new_today": g["new_today"], "split": g["split"],
                   "top_rising": [f"{r['skill']} (+{r['d7']}pp)" for r in g["top_rising"]]},
        "buckets": buckets,
    }


def deterministic_ideal(lv: dict[str, Any]) -> str:
    ex = [f"{e['trait']} ({e['share']:.0f}%)" for e in lv.get("expectations", [])[:4]]
    sk = [f"{s['skill']} ({s['share']:.0f}%)" for s in lv["top_skills"][:3]]
    parts = []
    if sk:
        parts.append("Most-requested skills: " + ", ".join(sk) + ".")
    if ex:
        parts.append("Postings most often ask for: " + ", ".join(ex) + ".")
    return " ".join(parts) or "Not enough data yet."


def deterministic_glance(data: dict[str, Any]) -> str:
    g, s = data["glance"], data["glance"]["split"]
    txt = (f"{g['total_postings']} postings in the {g['ttl_days']}-day snapshot ({g['new_today']} new today). "
           f"{s['preferred_total']} in {s['preferred_city']}, {s['india_remote']} India-remote, "
           f"{s['hybrid_elsewhere']} hybrid elsewhere in India.")
    if g["top_rising"]:
        txt += " Top riser for L1–L2: " + g["top_rising"][0]["skill"] + "."
    return txt


def write_prose(data: dict[str, Any], llm: LLMClient | None) -> dict[str, Any]:
    """Fills data['glance']['paragraph'] and each primary level's 'ideal_candidate' in place."""
    by_key = {_bucket_key(f["family"], lv["level"]): lv for f in data["families"] for lv in f["levels"] if lv.get("primary")}
    for lv in by_key.values():
        lv["ideal_candidate"] = deterministic_ideal(lv)
    data["glance"]["paragraph"] = deterministic_glance(data)
    data["prose_source"] = "deterministic"
    if llm is None or not by_key:
        return data
    payload = json.dumps(prose_payload(data), ensure_ascii=False)
    try:
        llm.guard("summary", len(payload) + len(INSTRUCTIONS), 3000)
        out = llm.extract_structured(payload, ReportProse, instructions=INSTRUCTIONS, purpose="summary",
                                     max_output_tokens=3000)
    except Exception as e:  # noqa: BLE001 — prose is optional; numbers are already done
        log.warning("prose generation failed; using deterministic text", extra={"error": str(e)[:300]})
        return data
    if out.glance_paragraph.strip():
        data["glance"]["paragraph"] = out.glance_paragraph.strip()
    for ic in out.ideal_candidates:
        if ic.bucket in by_key and ic.paragraph.strip():
            by_key[ic.bucket]["ideal_candidate"] = ic.paragraph.strip()
    data["prose_source"] = llm.provider
    return data
