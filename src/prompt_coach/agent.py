"""A thin Agent over Google ADK. All ADK plumbing lives here and nowhere else."""

from __future__ import annotations

import time
import uuid
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm, LiteLLMClient
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from prompt_coach import models
from prompt_coach.types import AgentName, Case, Record

_APP_NAME = "prompt_coach"


class _Client(LiteLLMClient):
    """ADK's LiteLlm client hook, routed through ``models.complete`` so tests can fake it."""

    async def acompletion(self, model: str, messages: list[Any], tools: Any = None, **kwargs: Any) -> Any:
        if tools:
            kwargs["tools"] = tools
        return await models.complete(model, messages, **kwargs)

    def completion(self, model: str, messages: list[Any], tools: Any = None, **kwargs: Any) -> Any:
        raise NotImplementedError("prompt-coach only uses the async path")


class Agent:
    """One named agent: a model string, a prompt, and a prompt version. Stateless between cases."""

    def __init__(self, name: AgentName, model: str, prompt: str, version: int = 1) -> None:
        self.name: AgentName = name
        self.model = model
        self.prompt = prompt
        self.version = version

    async def run(self, case: Case) -> Record:
        """Run the agent on one case in a fresh session and return its reply."""
        llm_agent = LlmAgent(
            name=self.name,
            model=LiteLlm(model=self.model, llm_client=_Client()),
            instruction=self.prompt,
        )
        runner = InMemoryRunner(agent=llm_agent, app_name=_APP_NAME)
        started = time.perf_counter()
        try:
            session = await runner.session_service.create_session(
                app_name=_APP_NAME, user_id="customer", session_id=uuid.uuid4().hex
            )
            message = genai_types.Content(role="user", parts=[genai_types.Part(text=case.input)])
            reply_parts: list[str] = []
            async for event in runner.run_async(
                user_id=session.user_id, session_id=session.id, new_message=message
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    reply_parts.extend(p.text for p in event.content.parts if p.text)
        finally:
            await runner.close()
        reply = "".join(reply_parts).strip()
        if not reply:
            raise RuntimeError(f"{self.name} ({self.model}) returned an empty reply for case '{case.id}'")
        return Record(
            agent=self.name,
            model=self.model,
            prompt_version=self.version,
            case_id=case.id,
            reply=reply,
            latency_s=round(time.perf_counter() - started, 2),
        )
