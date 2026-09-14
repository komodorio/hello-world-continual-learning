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


# A round fires every agent, evaluator and coach call at once, and providers answer a burst that
# size with the occasional 503 (Bedrock does). One unretried failure aborts the whole round, so
# retry here rather than at any call site; LiteLLM backs off exponentially between attempts.
NUM_RETRIES = 3


async def complete(model: str, messages: list[Message], **kwargs: Any) -> Any:
    """Call a LiteLLM model string with OpenAI-style messages and return the raw response."""
    kwargs.setdefault("num_retries", NUM_RETRIES)
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


def _messages(system: str, user: str) -> list[Message]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def complete_text(model: str, system: str, user: str, **kwargs: Any) -> str:
    """Convenience: one system + one user message in, assistant text out."""
    response = await complete(model, _messages(system, user), **kwargs)
    return text_of(response)


async def complete_json(model: str, system: str, user: str, *, attempts: int = 3, **kwargs: Any) -> dict[str, Any]:
    """Ask for a JSON object; retry a malformed reply, then raise with the evidence attached.

    Models occasionally emit an unterminated or mangled object on the last few characters
    (observed with finish_reason == "stop", so ``text_of`` lets it through). The failure that
    reaches the caller carries the finish reason and the length it got to, because "invalid JSON"
    on its own cannot tell a truncated reply apart from a model that ignored the format.
    """
    last_error: ModelOutputError | None = None
    for _ in range(attempts):
        response = await complete(model, _messages(system, user), **kwargs)
        text = text_of(response)
        try:
            return extract_json_object(text)
        except ModelOutputError as exc:
            finish = response.choices[0].finish_reason
            last_error = type(exc)(f"{exc} [finish_reason={finish!r}, {len(text)} chars, model={model}]")
    assert last_error is not None
    raise last_error
