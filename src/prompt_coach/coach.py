"""The coach: reads the student's low-scored runs and proposes the next prompt version."""

from __future__ import annotations

from typing import Any

from prompt_coach import models
from prompt_coach.models import ModelOutputError
from prompt_coach.prompts import load_prompt
from prompt_coach.types import Graded, PromptVersion


class CoachOutputError(ModelOutputError):
    """The coach did not return the JSON object we asked for."""


def select_failures(graded: list[Graded], threshold: float) -> list[Graded]:
    """The student runs the coach is allowed to see: strictly below the threshold."""
    return [g for g in graded if g.record.agent == "student" and g.verdict.score < threshold]


def _coach_input(
    teacher_prompt: str,
    student_prompt: PromptVersion,
    failures: list[Graded],
    case_inputs: dict[str, str],
    recommendation: str,
    history: list[str],
) -> str:
    parts = [
        f"## Teacher prompt (reference)\n{teacher_prompt.strip()}\n",
        f"## Student prompt v{student_prompt.version} (current, {len(student_prompt.text.split())} words)\n{student_prompt.text.strip()}\n",
        "## Prompt history (version, mean score, what changed)\n" + "\n".join(history or ["(first round)"]) + "\n",
        f"## Grader's recommendation\n{recommendation.strip() or '(none)'}\n",
        "## Student replies that scored poorly this round\n",
    ]
    for g in failures:
        parts.append(
            f"### case {g.record.case_id} - score {g.verdict.score:.2f}\n"
            f"Input the student saw:\n{case_inputs[g.record.case_id].strip()}\n\n"
            f"Student reply:\n{g.record.reply.strip()}\n\n"
            f"Grader's reason:\n{g.verdict.reason.strip()}\n"
        )
    if not failures:
        parts.append("(no student reply fell below the threshold; tighten the prompt using the recommendation)\n")
    return "\n".join(parts)


def parse_proposal(text: str, current_version: int) -> PromptVersion:
    try:
        return proposal_from(models.extract_json_object(text), current_version)
    except ModelOutputError as exc:
        raise CoachOutputError(str(exc)) from exc


def proposal_from(data: dict[str, Any], current_version: int) -> PromptVersion:
    prompt, changelog = data.get("prompt"), data.get("changelog")
    if not isinstance(prompt, str) or not prompt.strip():
        raise CoachOutputError(f"coach returned no 'prompt': {data!r}"[:300])
    if not isinstance(changelog, str) or not changelog.strip():
        raise CoachOutputError(f"coach returned no 'changelog': {data!r}"[:300])
    return PromptVersion(version=current_version + 1, text=prompt.strip(), changelog=changelog.strip())


async def propose(
    teacher_prompt: str,
    student_prompt: PromptVersion,
    failures: list[Graded],
    recommendation: str,
    *,
    case_inputs: dict[str, str],
    history: list[str] | None = None,
    model: str,
) -> PromptVersion:
    """Ask the coach for prompt v+1. It gets case inputs and replies only; ``expected`` is never passed in.

    ``history`` is one line per earlier version ("v2 mean 0.60: <changelog>") so the coach can
    see which changes helped and which hurt.
    """
    user = _coach_input(teacher_prompt, student_prompt, failures, case_inputs, recommendation, history or [])
    try:
        data = await models.complete_json(model, load_prompt("coach"), user, max_tokens=6000)
    except ModelOutputError as exc:
        raise CoachOutputError(str(exc)) from exc
    return proposal_from(data, student_prompt.version)
