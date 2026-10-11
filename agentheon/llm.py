"""The only place that talks to a model. LiteLLM gives us any provider by changing LLM_MODEL."""

from __future__ import annotations

import json
import re
from typing import Protocol


class LLM(Protocol):
    async def complete(self, messages: list[dict], *, json_mode: bool = False) -> str: ...


class LiteLLMClient:
    """Examples of LLM_MODEL: gpt-4o-mini, anthropic/claude-sonnet-5, ollama/llama3.1, azure/<deployment>.

    Two deliberate choices keep this working across providers:

    - `temperature` is only sent when LLM_TEMPERATURE is set. Recent reasoning models (Claude
      Sonnet 5, Claude Opus 5 and the Opus 4.7+ family) reject a non-default temperature with a
      400, and `drop_params` does not help because temperature is a valid parameter for the
      provider in general. Leaving it unset gets each provider's own default.
    - `max_tokens` is always sent and defaults generously. On models that think before answering,
      it caps thinking *and* the reply together, so a tight budget truncates the JSON this
      platform depends on.
    """

    def __init__(self, model: str, temperature: float | None = None, max_tokens: int = 8192,
                 timeout: float = 120):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    async def complete(self, messages: list[dict], *, json_mode: bool = False) -> str:
        import litellm

        litellm.drop_params = True  # silently skip params a provider does not support
        kwargs: dict = {"max_tokens": self.max_tokens}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        response = await litellm.acompletion(
            model=self.model,
            messages=messages,
            timeout=self.timeout,
            num_retries=2,
            **kwargs,
        )
        return response.choices[0].message.content or ""


class LLMOutputError(ValueError):
    """The model did not return parseable JSON."""


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles code fences and chatter around it)."""
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for start in [i for i, ch in enumerate(candidate) if ch == "{"]:
            try:
                value, _ = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise LLMOutputError(f"Expected a JSON object, got: {text[:200]!r}")


async def complete_json(llm: LLM, messages: list[dict]) -> dict:
    """Ask for JSON; if the reply is not JSON, ask once more with the error, then give up."""
    reply = await llm.complete(messages, json_mode=True)
    try:
        return parse_json(reply)
    except LLMOutputError:
        retry = messages + [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": "That was not a valid JSON object. Reply with only the JSON object."},
        ]
        return parse_json(await llm.complete(retry, json_mode=True))
