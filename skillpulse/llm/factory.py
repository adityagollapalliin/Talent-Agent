"""Build the configured LLM client."""

from __future__ import annotations

from skillpulse.config import Secrets, Settings
from skillpulse.extract.aliases import Aliases
from skillpulse.llm.base import CostTracker, LLMClient


def build_llm(settings: Settings, secrets: Secrets, aliases: Aliases, dry_run: bool = False) -> LLMClient:
    if dry_run:
        from skillpulse.llm.fake import FakeLLM

        return FakeLLM(aliases)
    m = settings.llm.models()
    tracker = CostTracker(max_usd=settings.max_cost_usd_per_run, pricing=dict(m.pricing))
    if settings.llm.provider == "openai":
        if not secrets.openai_api_key:
            raise RuntimeError("llm.provider is openai but OPENAI_API_KEY is not set")
        from skillpulse.llm.openai_client import OpenAIClient

        return OpenAIClient(secrets.openai_api_key, m.extraction_model, m.summary_model, tracker,
                            country_code=settings.region.country_code, city=settings.region.preferred_city)
    if settings.llm.provider == "anthropic":
        if not secrets.anthropic_api_key:
            raise RuntimeError("llm.provider is anthropic but ANTHROPIC_API_KEY is not set")
        from skillpulse.llm.anthropic_client import AnthropicClient

        return AnthropicClient(secrets.anthropic_api_key, m.extraction_model, m.summary_model, tracker,
                               country_code=settings.region.country_code, city=settings.region.preferred_city)
    raise ValueError(f"unknown llm.provider {settings.llm.provider!r}")
