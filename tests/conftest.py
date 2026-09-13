"""Shared fixtures: a FakeModel that stands in for every LiteLLM call, and a test Config."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from litellm import ModelResponse

from prompt_coach import llm
from prompt_coach.config import Config, LoopSettings, Models
from prompt_coach.task import load_task
from prompt_coach.types import Case, Task

REPO = Path(__file__).resolve().parent.parent
SUPPORT_TASK = REPO / "tasks" / "support"

FAKE_MODELS = Models(teacher="fake/teacher", student="fake/student", evaluator="fake/judge", coach="fake/coach")


def _response(text: str) -> ModelResponse:
    return ModelResponse(
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]
    )


class FakeModel:
    """Canned replies keyed by the LiteLLM model string the code asked for.

    - fake/teacher and fake/student reply with a tagged line so the fake judge can tell them
      apart (the real judge is blind; the tag is only for scripting scores).
    - fake/judge returns judge JSON: teacher scores from ``teacher_scores``; student scores
      from ``student_scores[round]`` where the round is the student's prompt version - 1.
    - fake/coach returns a new prompt carrying a version marker, so the student's next replies
      can be scored from the next row of ``student_scores``.
    Every call is recorded in ``calls`` for assertions.
    """

    def __init__(self, cases: list[Case]) -> None:
        self.cases = cases
        self.calls: list[dict[str, Any]] = []
        self.teacher_scores: dict[str, float] = {c.id: 0.9 for c in cases}
        self.student_scores: list[dict[str, float]] = [{c.id: 0.5 for c in cases}]
        self.judge_text: str | None = None  # override to return garbage etc.
        self.coach_text: str | None = None
        self.recommendation = "The student misses required details and runs long."

    def _case_for(self, text: str) -> Case:
        for case in self.cases:
            if case.input.strip() in text:
                return case
        raise AssertionError("fake judge could not find the case input in its prompt")

    async def __call__(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> ModelResponse:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        user = "\n".join(str(m["content"]) for m in messages if m["role"] == "user")
        self.calls.append({"model": model, "system": system, "user": user, "messages": messages})
        return _response(self._reply(model, system, user))

    def _reply(self, model: str, system: str, user: str) -> str:
        if model in ("fake/teacher", "fake/student"):
            role = model.split("/")[1]
            marker = re.search(r"\[v(\d+)\]", system)
            version = int(marker.group(1)) if marker else 1
            return f"[{role} v{version}] Hi there, here is my reply."
        if model == "fake/judge":
            if "compare two AI support agents" in system:
                return self.recommendation
            if self.judge_text is not None:
                return self.judge_text
            tag = re.search(r"\[(teacher|student) v(\d+)\]", user)
            assert tag, "fake judge did not find a tagged reply"
            case = self._case_for(user)
            if tag.group(1) == "teacher":
                score = self.teacher_scores[case.id]
            else:
                row = min(int(tag.group(2)) - 1, len(self.student_scores) - 1)
                score = self.student_scores[row][case.id]
            return json.dumps({"score": score, "reason": f"fake reason for {tag.group(1)} on {case.id}"})
        if model == "fake/coach":
            if self.coach_text is not None:
                return self.coach_text
            current = re.search(r"## Student prompt v(\d+)", user)
            assert current, "fake coach did not find the current version"
            nxt = int(current.group(1)) + 1
            return json.dumps(
                {"prompt": f"[v{nxt}] Be brief and answer every question.", "changelog": f"fake change for v{nxt}"}
            )
        raise AssertionError(f"unexpected model in test: {model}")


@pytest.fixture
def support_task() -> Task:
    return load_task(SUPPORT_TASK)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, support_task: Task) -> FakeModel:
    model = FakeModel(support_task.cases)
    monkeypatch.setattr(llm, "complete", model)
    return model


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        models=FAKE_MODELS,
        task=SUPPORT_TASK,
        runs_dir=tmp_path / "runs",
        loop=LoopSettings(max_rounds=5, gap=0.1, coach_threshold=0.7),
    )
