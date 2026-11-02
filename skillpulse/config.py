"""Configuration loading: config.yaml (behaviour) + .env (secrets).

Everything domain-specific (titles, levels, technologies) comes from config.yaml.
Code never hardcodes those lists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


class Region(BaseModel):
    country: str = "India"
    country_code: str = "in"
    preferred_city: str = "Hyderabad"
    work_modes: list[str] = ["remote", "hybrid", "onsite"]
    include_india_remote: bool = True
    india_locations: list[str] = Field(default_factory=lambda: ["India"])


class RoleFamily(BaseModel):
    titles: list[str]


class ProviderModels(BaseModel):
    extraction_model: str
    summary_model: str
    pricing: dict[str, dict[str, float]] = Field(default_factory=dict)


class LLMConfig(BaseModel):
    provider: Literal["openai", "anthropic"] = "openai"
    openai: ProviderModels | None = None
    anthropic: ProviderModels | None = None

    def models(self) -> ProviderModels:
        m = getattr(self, self.provider)
        if m is None:
            raise ValueError(f"llm.{self.provider} block missing in config.yaml")
        return m


class SourceToggle(BaseModel):
    model_config = {"extra": "allow"}
    enabled: bool = True
    min_interval_s: float = 1.0

    def opt(self, key: str, default: Any = None) -> Any:
        return (self.model_extra or {}).get(key, default)


class CacheConfig(BaseModel):
    ttl_days: int = 45
    search_repeat_hours: int = 20  # a query already run within this window is not re-run


class ReportConfig(BaseModel):
    top_skills: int = 15
    top_skills_secondary: int = 10
    rising_threshold_pp: float = 5.0
    trend_epsilon_pp: float = 1.0  # |delta| below this shows "="
    sample_links: int = 3


class Schedule(BaseModel):
    timezone: str = "Asia/Kolkata"
    run_at: str = "06:30"


class EmailConfig(BaseModel):
    enabled: bool = True
    attach_docx: bool = True
    html_body: bool = True
    smtp_host: str = "smtp.zoho.in"
    smtp_port: int = 465


class Delivery(BaseModel):
    email: EmailConfig = EmailConfig()


class Settings(BaseModel):
    region: Region = Region()
    employment_types: list[str] = ["full-time"]
    role_families: dict[str, RoleFamily]
    report_levels: list[str] = ["L1", "L2", "L3"]
    primary_levels: list[str] = ["L1", "L2"]
    focus_technologies: dict[str, list[str]] = Field(default_factory=dict)
    max_postings_per_title_per_run: int = 25
    llm: LLMConfig = LLMConfig()
    max_cost_usd_per_run: float = 1.0
    summary_reserve_usd: float = 0.10
    sources: dict[str, SourceToggle] = Field(default_factory=dict)
    cache: CacheConfig = CacheConfig()
    report: ReportConfig = ReportConfig()
    schedule: Schedule = Schedule()
    delivery: Delivery = Delivery()

    def source(self, name: str) -> SourceToggle:
        return self.sources.get(name, SourceToggle(enabled=False))

    def focus_list(self) -> list[str]:
        return [t for techs in self.focus_technologies.values() for t in techs]


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path
    reports: Path
    logs: Path
    memory: Path
    config_dir: Path
    fixtures: Path

    @property
    def db(self) -> Path:
        return self.data / "skillpulse.db"

    @property
    def lock(self) -> Path:
        return self.data / "skillpulse.lock"

    @classmethod
    def at(cls, root: Path, dry_run: bool = False) -> "Paths":
        data = root / "data"
        reports = root / "reports"
        if dry_run:  # keep fixture runs out of the real DB and report archive
            data = data / "dry-run"
            reports = reports / "dry-run"
        return cls(
            root=root,
            data=data,
            reports=reports,
            logs=root / "logs",
            memory=root / "memory",
            config_dir=root / "config",
            fixtures=root / "tests" / "fixtures",
        )

    def ensure(self) -> None:
        for p in (self.data, self.data / "backups", self.reports, self.logs, self.memory):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Secrets:
    openai_api_key: str | None
    anthropic_api_key: str | None
    adzuna_app_id: str | None
    adzuna_app_key: str | None
    zoho_user: str | None
    zoho_app_password: str | None
    report_to: str | None
    serpapi_key: str | None

    @classmethod
    def from_env(cls) -> "Secrets":
        g = lambda k: (os.environ.get(k) or "").strip() or None  # noqa: E731
        return cls(
            openai_api_key=g("OPENAI_API_KEY"),
            anthropic_api_key=g("ANTHROPIC_API_KEY"),
            adzuna_app_id=g("ADZUNA_APP_ID"),
            adzuna_app_key=g("ADZUNA_APP_KEY"),
            zoho_user=g("ZOHO_SMTP_USER"),
            zoho_app_password=g("ZOHO_SMTP_APP_PASSWORD"),
            report_to=g("REPORT_TO"),
            serpapi_key=g("SERPAPI_API_KEY"),
        )


def load_settings(path: Path | None = None) -> Settings:
    path = path or ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Settings.model_validate(raw)


def load_secrets(root: Path = ROOT) -> Secrets:
    load_dotenv(root / ".env", override=False)
    return Secrets.from_env()


def load_companies(path: Path) -> dict[str, list[dict[str, str]]]:
    """config/companies.yaml -> {"greenhouse": [{name, token}], "lever": [...], "ashby": [...]}"""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return {k: list(v or []) for k, v in raw.items()}
