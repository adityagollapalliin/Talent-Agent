"""Anthropic implementation (Messages API + native structured outputs via Pydantic).

- Extraction (cheap model): `client.messages.parse(output_format=Model)`, effort "low".
- Summary / web search (stronger model): beta Messages API with server-side refusal fallbacks
  (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`). Cost is priced on the model that actually served.
- Web search: server tool `web_search_20260209`. Real URLs come from `web_search_tool_result` blocks.
"""

from __future__ import annotations

import json
import re
from typing import Any

from skillpulse.llm.base import CostTracker, LLMClient, LLMRefusal, Purpose, T

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = "web_search_20260209"
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self, api_key: str, extraction_model: str, summary_model: str, tracker: CostTracker,
                 country_code: str = "IN", city: str | None = None, client: Any = None,
                 extraction_effort: str = "low", summary_effort: str = "medium"):
        super().__init__(tracker)
        import anthropic

        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.extraction_model = extraction_model
        self.summary_model = summary_model
        self.extraction_effort = extraction_effort
        self.summary_effort = summary_effort
        self.user_location = {"type": "approximate", "country": country_code.upper(), **({"city": city} if city else {})}

    def _usage(self, requested: str, resp: Any) -> None:
        u = resp.usage
        inp = (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) + \
              (getattr(u, "cache_read_input_tokens", 0) or 0)
        served = getattr(resp, "model", None) or requested
        self._record(served if served in self.tracker.pricing else requested, inp, u.output_tokens or 0)

    @staticmethod
    def _check(resp: Any) -> None:
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise LLMRefusal(f"model refused (category={cat})")

    @staticmethod
    def _text(resp: Any) -> str:
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

    def extract_structured(self, text: str, schema: type[T], *, instructions: str,
                           purpose: Purpose = "extraction", max_output_tokens: int = 2000) -> T:
        model = self.model_for(purpose)
        # Thinking tokens count toward max_tokens, so leave headroom above the answer size.
        max_tokens = max(8000, max_output_tokens * 4)
        if purpose == "extraction":
            resp = self.client.messages.parse(
                model=model, max_tokens=max_tokens, system=instructions,
                messages=[{"role": "user", "content": text}], output_format=schema,
                output_config={"effort": self.extraction_effort},
            )
        else:
            resp = self.client.beta.messages.parse(
                model=model, max_tokens=max(16000, max_tokens), system=instructions,
                messages=[{"role": "user", "content": text}], output_format=schema,
                output_config={"effort": self.summary_effort},
                betas=[FALLBACK_BETA], fallbacks="default",
            )
        self._usage(model, resp)
        self._check(resp)
        if resp.parsed_output is None:
            raise LLMRefusal(f"no parsed output (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    def summarise(self, prompt: str, *, instructions: str = "", max_output_tokens: int = 4000) -> str:
        model = self.summary_model
        kwargs: dict[str, Any] = {"system": instructions} if instructions else {}
        resp = self.client.beta.messages.create(
            model=model, max_tokens=max(16000, max_output_tokens * 4),
            messages=[{"role": "user", "content": prompt}], output_config={"effort": self.summary_effort},
            betas=[FALLBACK_BETA], fallbacks="default", **kwargs,
        )
        self._usage(model, resp)
        self._check(resp)
        return self._text(resp)

    def web_search(self, query: str, schema: type[T], *, instructions: str) -> tuple[T, set[str]]:
        """Server-side web search. Citations and structured output don't mix, so the model is asked for a fenced JSON
        object that is validated with Pydantic. Handles `pause_turn` by resending the turn (max 3 times)."""
        model = self.summary_model
        tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 5, "user_location": self.user_location}]
        system = instructions + "\n\nReturn ONLY a ```json fenced object matching this JSON schema:\n" + \
            json.dumps(schema.model_json_schema())
        messages: list[dict[str, Any]] = [{"role": "user", "content": query}]
        urls: set[str] = set()
        resp = None
        for _ in range(4):
            resp = self.client.beta.messages.create(
                model=model, max_tokens=16000, system=system, messages=messages, tools=tools,
                output_config={"effort": self.summary_effort}, betas=[FALLBACK_BETA], fallbacks="default",
            )
            self._usage(model, resp)
            self._check(resp)
            for block in resp.content:
                if getattr(block, "type", None) == "web_search_tool_result" and isinstance(block.content, list):
                    urls.update(r.url for r in block.content if getattr(r, "url", None))
            if resp.stop_reason != "pause_turn":
                break
            messages = [*messages, {"role": "assistant", "content": resp.content}]
        assert resp is not None
        text = self._text(resp)
        m = _JSON_BLOCK.search(text)
        raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
        try:
            return schema.model_validate_json(raw), urls
        except Exception as e:  # noqa: BLE001
            raise LLMRefusal(f"web search answer was not valid JSON for {schema.__name__}: {e}") from e
