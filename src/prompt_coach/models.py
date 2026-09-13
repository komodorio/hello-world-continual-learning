"""The single place that talks to LiteLLM.

Every model call in the project (agents via ADK, evaluator, coach) goes through
``complete``; tests replace it with a fake to run offline.
"""

from __future__ import annotations

from typing import Any

import litellm

Message = dict[str, Any]


async def complete(model: str, messages: list[Message], **kwargs: Any) -> Any:
    """Call a LiteLLM model string with OpenAI-style messages and return the raw response."""
    return await litellm.acompletion(model=model, messages=messages, **kwargs)


def text_of(response: Any) -> str:
    """Extract the assistant text from a LiteLLM ModelResponse."""
    content = response.choices[0].message.content
    if not isinstance(content, str):
        raise ValueError(f"model returned no text content: {content!r}")
    return content


async def complete_text(model: str, system: str, user: str, **kwargs: Any) -> str:
    """Convenience: one system + one user message in, assistant text out."""
    messages: list[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    response = await complete(model, messages, **kwargs)
    return text_of(response)
