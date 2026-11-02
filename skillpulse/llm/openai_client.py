"""OpenAI implementation (Responses API + native JSON-schema structured output via Pydantic)."""

from __future__ import annotations

from typing import Any

from skillpulse.llm.base import CostTracker, LLMClient, LLMRefusal, Purpose, T


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(self, api_key: str, extraction_model: str, summary_model: str, tracker: CostTracker,
                 country_code: str = "IN", city: str | None = None, client: Any = None):
        super().__init__(tracker)
        from openai import OpenAI

        self.client = client or OpenAI(api_key=api_key, max_retries=4, timeout=120)
        self.extraction_model = extraction_model
        self.summary_model = summary_model
        self.user_location = {"type": "approximate", "country": country_code.upper(), **({"city": city} if city else {})}

    def _usage(self, model: str, resp: Any) -> None:
        u = getattr(resp, "usage", None)
        self._record(model, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)

    def extract_structured(self, text: str, schema: type[T], *, instructions: str,
                           purpose: Purpose = "extraction", max_output_tokens: int = 2000) -> T:
        model = self.model_for(purpose)
        resp = self.client.responses.parse(
            model=model, instructions=instructions, input=text, text_format=schema,
            max_output_tokens=max_output_tokens,
        )
        self._usage(model, resp)
        parsed = resp.output_parsed
        if parsed is None:
            raise LLMRefusal(f"no parsed output (status={getattr(resp, 'status', '?')})")
        return schema.model_validate(parsed.model_dump()) if not isinstance(parsed, schema) else parsed

    def summarise(self, prompt: str, *, instructions: str = "", max_output_tokens: int = 4000) -> str:
        model = self.summary_model
        resp = self.client.responses.create(
            model=model, instructions=instructions or None, input=prompt, max_output_tokens=max_output_tokens,
        )
        self._usage(model, resp)
        return (resp.output_text or "").strip()

    def web_search(self, query: str, schema: type[T], *, instructions: str) -> tuple[T, set[str]]:
        model = self.summary_model
        resp = self.client.responses.parse(
            model=model, instructions=instructions, input=query, text_format=schema,
            tools=[{"type": "web_search", "user_location": self.user_location}],
            include=["web_search_call.action.sources"], max_output_tokens=6000,
        )
        self._usage(model, resp)
        urls: set[str] = set()
        for item in resp.output or []:
            if getattr(item, "type", None) == "web_search_call":
                for s in getattr(getattr(item, "action", None), "sources", None) or []:
                    if getattr(s, "url", None):
                        urls.add(s.url)
            for c in getattr(item, "content", None) or []:
                for a in getattr(c, "annotations", None) or []:
                    if getattr(a, "type", None) == "url_citation" and getattr(a, "url", None):
                        urls.add(a.url)
        if resp.output_parsed is None:
            raise LLMRefusal("web search returned no parsed output")
        return resp.output_parsed, urls
