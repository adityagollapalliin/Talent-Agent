"""Provider-neutral LLM interface + cost tracking.

Implementations: openai_client.OpenAIClient (Responses API), anthropic_client.AnthropicClient (Messages API),
fake.FakeLLM (dry runs / tests). All structured output is validated with Pydantic.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from pydantic import BaseModel

from skillpulse.models import LevelResult

log = logging.getLogger("skillpulse.llm")
T = TypeVar("T", bound=BaseModel)
Purpose = Literal["extraction", "summary"]
FALLBACK_PRICE = {"input": 10.0, "output": 50.0}  # deliberately pessimistic for unknown models


class BudgetExceeded(Exception):
    pass


class LLMRefusal(Exception):
    pass


@dataclass
class CostTracker:
    max_usd: float
    pricing: dict[str, dict[str, float]]
    spent_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    by_model: dict[str, float] = field(default_factory=dict)

    def price(self, model: str) -> dict[str, float]:
        p = self.pricing.get(model)
        if p is None:
            log.warning("no pricing for model; using pessimistic fallback", extra={"model": model})
            return FALLBACK_PRICE
        return p

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.price(model)
        return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000

    def add(self, model: str, input_tokens: int, output_tokens: int) -> float:
        c = self.cost(model, input_tokens, output_tokens)
        self.spent_usd += c
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1
        self.by_model[model] = self.by_model.get(model, 0.0) + c
        return c

    def estimate(self, model: str, prompt_chars: int, max_output_tokens: int) -> float:
        return self.cost(model, int(prompt_chars / 3.5) + 50, max_output_tokens)

    def can_afford(self, estimate_usd: float, reserve_usd: float = 0.0) -> bool:
        return self.spent_usd + estimate_usd <= self.max_usd - reserve_usd

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.max_usd - self.spent_usd)


@dataclass
class CallResult:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str


class LevelAnswer(BaseModel):
    level: str
    confidence: Literal["high", "medium", "low"]
    years_min: float | None
    years_max: float | None
    rationale: str


class LLMClient(ABC):
    provider: str
    extraction_model: str
    summary_model: str

    def __init__(self, tracker: CostTracker):
        self.tracker = tracker
        self.last_call: CallResult | None = None

    def model_for(self, purpose: Purpose) -> str:
        return self.extraction_model if purpose == "extraction" else self.summary_model

    def _record(self, model: str, input_tokens: int, output_tokens: int) -> CallResult:
        c = self.tracker.add(model, input_tokens, output_tokens)
        self.last_call = CallResult(input_tokens, output_tokens, c, model)
        log.info("llm call", extra={"provider": self.provider, "model": model, "in": input_tokens,
                                    "out": output_tokens, "cost_usd": round(c, 5),
                                    "spent_usd": round(self.tracker.spent_usd, 4)})
        return self.last_call

    def guard(self, purpose: Purpose, prompt_chars: int, max_output_tokens: int, reserve_usd: float = 0.0) -> None:
        est = self.tracker.estimate(self.model_for(purpose), prompt_chars, max_output_tokens)
        if not self.tracker.can_afford(est, reserve_usd):
            raise BudgetExceeded(f"next {purpose} call (~${est:.4f}) would exceed budget "
                                 f"(spent ${self.tracker.spent_usd:.4f} of ${self.tracker.max_usd:.2f})")

    @abstractmethod
    def extract_structured(self, text: str, schema: type[T], *, instructions: str,
                           purpose: Purpose = "extraction", max_output_tokens: int = 2000) -> T:
        """Return `schema` parsed from the model's native structured output."""

    @abstractmethod
    def summarise(self, prompt: str, *, instructions: str = "", max_output_tokens: int = 4000) -> str:
        """Free-text write-up with the stronger summary model."""

    def web_search(self, query: str, schema: type[T], *, instructions: str) -> tuple[T, set[str]]:
        """Run the provider's built-in web search; return (parsed result, URLs the search tool actually returned)."""
        raise NotImplementedError(f"{self.provider} client has no web search")

    def classify_level(self, title: str, description: str, rubric_text: str, levels: list[str],
                       reserve_usd: float = 0.0) -> LevelResult | None:
        """Last-resort level classification constrained to the rubric. None if over budget or invalid."""
        text = f"Title: {title}\n\nDescription:\n{description[:6000]}"
        instr = (
            "Classify the job posting's seniority level using ONLY this rubric. "
            f"Answer `level` with exactly one of: {', '.join(levels)}. Extract years_min/years_max if stated, else null.\n\n"
            + rubric_text
        )
        try:
            self.guard("extraction", len(text) + len(instr), 300, reserve_usd)
            ans = self.extract_structured(text, LevelAnswer, instructions=instr, max_output_tokens=300)
        except BudgetExceeded:
            return None
        except Exception:  # noqa: BLE001
            log.exception("llm level classification failed")
            return None
        if ans.level not in levels:
            return None
        return LevelResult(ans.level, ans.confidence, ans.years_min, ans.years_max,
                           [f"llm:{ans.rationale[:120]}"])
