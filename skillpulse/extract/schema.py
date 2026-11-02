"""Per-posting extraction schema. All fields are required (nullable where noted) so it is valid
for both OpenAI strict JSON schema and Anthropic structured outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JobExtraction(BaseModel):
    must_have_skills: list[str] = Field(description="Required skills/technologies, canonical names")
    nice_to_have_skills: list[str] = Field(description="Preferred / plus / bonus skills, canonical names")
    certifications: list[str] = Field(description="Certifications named in the posting, full official names")
    languages: list[str] = Field(description="Programming / query languages")
    frameworks: list[str] = Field(description="Libraries and frameworks")
    cloud: list[str] = Field(description="Cloud providers and their services")
    ai_platforms: list[str] = Field(description="LLM / AI platforms and model providers")
    data_platforms: list[str] = Field(description="Data warehouses, lakehouses, processing, orchestration")
    tools: list[str] = Field(description="Other tools: DevOps, BI, observability, version control, etc.")
    soft_skills_and_expectations: list[str] = Field(description="Ideal-candidate traits, short phrases")
    education_requirement: str | None = Field(description="Degree requirement as stated, or null")
    years_min: float | None
    years_max: float | None
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"]
    employment_type: Literal["full-time", "internship", "contract", "part-time", "unknown"]
    city: str | None
    matches_focus_technologies: list[str] = Field(description="Subset of the given focus list that the posting asks for")


SKILL_LIST_FIELDS = ("must_have_skills", "nice_to_have_skills", "languages", "frameworks", "cloud",
                     "ai_platforms", "data_platforms", "tools")
