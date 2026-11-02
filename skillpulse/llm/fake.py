"""Deterministic offline LLM for --dry-run and tests. Makes no network calls and costs nothing.

Extraction is a keyword scan against the alias table, so dry-run reports look realistic.
"""

from __future__ import annotations

import json
import re
from typing import Any

from skillpulse.extract.aliases import Aliases, _key
from skillpulse.extract.schema import JobExtraction
from skillpulse.llm.base import CostTracker, LevelAnswer, LLMClient, Purpose, T

_NICE = re.compile(r"\b(nice to have|a plus|is a plus|preferred|bonus|good to have|big plus)\b", re.I)
_CERT = re.compile(r"(AWS Certified [A-Z][\w ]+?(?:Associate|Specialty|Practitioner|Professional)"
                   r"|AWS Certified Solutions Architect - Associate|\bCKA\b|\bAI-102\b|\bPL-300\b"
                   r"|Terraform Associate|SnowPro\w*|Google Data Analytics certificate)", re.I)
_IDEAL = re.compile(r"(?:ideal candidate|we want someone with|you are)[:\s]+([^\n]+)", re.I)


class FakeLLM(LLMClient):
    provider = "fake"
    extraction_model = "fake-extract"
    summary_model = "fake-summary"

    def __init__(self, aliases: Aliases, tracker: CostTracker | None = None):
        super().__init__(tracker or CostTracker(max_usd=1e9, pricing={}))
        self.aliases = aliases
        self.tracker.pricing.setdefault(self.extraction_model, {"input": 0.0, "output": 0.0})
        self.tracker.pricing.setdefault(self.summary_model, {"input": 0.0, "output": 0.0})
        names = sorted(self.aliases.map.items(), key=lambda kv: -len(kv[0]))
        self._terms = [(re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])"), v) for k, v in names if len(k) > 1]

    def _scan(self, text: str) -> list[str]:
        k = " " + _key(text) + " "
        found: list[str] = []
        for pat, canon in self._terms:
            if pat.search(k) and canon not in found:
                found.append(canon)
        return found

    def _extract(self, text: str) -> JobExtraction:
        desc = text.split("Description:", 1)[-1]
        must: list[str] = []
        nice: list[str] = []
        for sent in re.split(r"(?<=[.!?])\s+|\n", desc):
            hits = [h for h in self._scan(sent) if not re.search(r"certif|associate|specialty|practitioner|snowpro|\(", h, re.I)]
            (nice if _NICE.search(sent) else must).extend(h for h in hits if h not in must and h not in nice)
        certs = list(dict.fromkeys(self.aliases.canonical(m.group(1).strip()) for m in _CERT.finditer(desc)))
        ideal = []
        for m in _IDEAL.finditer(desc):
            ideal += [p.strip(" .") for p in re.split(r",| and ", m.group(1)) if 2 < len(p.strip()) < 60]
        lower = desc.lower()

        def cat(*words: str) -> list[str]:
            return [s for s in must + nice if any(w in s.lower() for w in words)]

        return JobExtraction(
            must_have_skills=must, nice_to_have_skills=nice, certifications=certs,
            languages=[s for s in must + nice if s in ("Python", "SQL", "Java", "Go", "TypeScript", "JavaScript", "Bash")],
            frameworks=cat("django", "fastapi", "spring", "pytorch", "scikit", "langchain", "pandas"),
            cloud=cat("aws", "azure", "google cloud", "ec2", "s3", "eks", "lambda"),
            ai_platforms=cat("openai", "claude", "gemini", "bedrock", "hugging"),
            data_platforms=cat("databricks", "snowflake", "bigquery", "spark", "dbt", "airflow"),
            tools=cat("docker", "kubernetes", "terraform", "ci/cd", "git", "power bi", "tableau", "excel", "grafana"),
            soft_skills_and_expectations=ideal,
            education_requirement="B.Tech/B.E. in CS or related" if "b.tech" in lower else None,
            years_min=None, years_max=None, work_mode="unknown", employment_type="unknown", city=None,
            matches_focus_technologies=[],
        )

    def extract_structured(self, text: str, schema: type[T], *, instructions: str,
                           purpose: Purpose = "extraction", max_output_tokens: int = 2000) -> T:
        self._record(self.model_for(purpose), len(text) // 4, 200)
        if schema is JobExtraction:
            return self._extract(text)  # type: ignore[return-value]
        if schema is LevelAnswer:
            return LevelAnswer(level="L2", confidence="low", years_min=None, years_max=None,  # type: ignore[return-value]
                               rationale="dry-run default")
        return schema.model_validate(self._default_for(schema, text))

    def _default_for(self, schema: type[T], text: str) -> dict[str, Any]:
        from skillpulse.report.prose import ReportProse

        if schema is ReportProse:
            payload = json.loads(text)
            return {
                "glance_paragraph": f"[dry run] {payload.get('glance', {}).get('total_postings', 0)} postings in the snapshot.",
                "ideal_candidates": [
                    {"bucket": b["bucket"], "paragraph": "[dry run] " + ", ".join(b.get("top_expectations", [])[:4])}
                    for b in payload.get("buckets", [])
                ],
            }
        raise NotImplementedError(schema.__name__)

    def summarise(self, prompt: str, *, instructions: str = "", max_output_tokens: int = 4000) -> str:
        self._record(self.summary_model, len(prompt) // 4, 100)
        return "[dry run] summary text"
