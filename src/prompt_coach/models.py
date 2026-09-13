"""The single place that talks to LiteLLM.

Every model call in the project (agents via ADK, evaluator, coach) goes through
``complete``; tests replace it with a fake to run offline.
"""

from __future__ import annotations

import json
from typing import Any

import litellm

Message = dict[str, Any]


class ModelOutputError(ValueError):
    """A model did not return the structured output we asked for."""


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull the single JSON object out of a model reply.

    Tolerates code fences, text after the object, and one missing closing brace. Nothing else.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    if start == -1:
        raise ModelOutputError(f"no JSON object in model output: {text[:200]!r}")
    candidate = stripped[start:]
    decoder = json.JSONDecoder()
    try:
        data, _ = decoder.raw_decode(candidate)
    except json.JSONDecodeError as exc:
        # Observed in the wild: the model ends with `..."` minus the final brace even with
        # finish_reason == "stop". Accept exactly that one missing brace; anything else is garbage.
        try:
            data, _ = decoder.raw_decode(candidate + "}")
        except json.JSONDecodeError:
            raise ModelOutputError(f"invalid JSON from model: {exc}: {text[:200]!r}") from exc
    if not isinstance(data, dict):
        raise ModelOutputError(f"model output is not a JSON object: {text[:200]!r}")
    return data


async def complete(model: str, messages: list[Message], **kwargs: Any) -> Any:
    """Call a LiteLLM model string with OpenAI-style messages and return the raw response."""
    return await litellm.acompletion(model=model, messages=messages, **kwargs)


def text_of(response: Any) -> str:
    """Extract the assistant text from a LiteLLM ModelResponse; fail loudly on truncation."""
    choice = response.choices[0]
    content = choice.message.content
    if not isinstance(content, str):
        raise ValueError(f"model returned no text content: {content!r}")
    if choice.finish_reason == "length":
        raise ValueError(
            f"model output was cut off at max_tokens (got {len(content)} chars); "
            "reasoning models spend tokens before answering, raise max_tokens"
        )
    return content


async def complete_text(model: str, system: str, user: str, **kwargs: Any) -> str:
    """Convenience: one system + one user message in, assistant text out."""
    messages: list[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    response = await complete(model, messages, **kwargs)
    return text_of(response)
