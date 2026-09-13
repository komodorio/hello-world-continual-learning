"""LLM-as-judge with ground truth: one score in [0, 1] plus a reason per reply."""

from __future__ import annotations

from prompt_coach import models
from prompt_coach.models import ModelOutputError
from prompt_coach.prompts import load_prompt
from prompt_coach.types import Case, Graded, Record, Task, Verdict


class JudgeOutputError(ModelOutputError):
    """The judge did not return the JSON object we asked for."""


def parse_verdict(text: str) -> Verdict:
    """Parse the judge's reply strictly; clamp the score to [0, 1]; raise on anything else."""
    try:
        data = models.extract_json_object(text)
    except ModelOutputError as exc:
        raise JudgeOutputError(str(exc)) from exc
    score, reason = data.get("score"), data.get("reason")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise JudgeOutputError(f"judge 'score' is not a number: {score!r}")
    if not isinstance(reason, str) or not reason.strip():
        raise JudgeOutputError(f"judge 'reason' is missing: {text[:200]!r}")
    return Verdict(score=min(1.0, max(0.0, float(score))), reason=reason.strip())


def _grade_input(record: Record, case: Case, task: Task) -> str:
    return (
        f"## Customer message and policy\n{case.input.strip()}\n\n"
        f"## Output format the agent was told to follow\n{task.output_format.strip() or '(none)'}\n\n"
        f"## Grading guidance for this task\n{task.grading_guidance.strip() or '(none)'}\n\n"
        f"## Expected (checklist)\n{case.expected.strip()}\n\n"
        f"## The agent's reply ({len(record.reply.split())} words)\n{record.reply.strip()}\n"
    )


async def grade(record: Record, case: Case, *, task: Task, model: str) -> Verdict:
    """Grade one reply against its case. The judge never learns which agent wrote it."""
    if record.case_id != case.id:
        raise ValueError(f"record is for case '{record.case_id}', not '{case.id}'")
    text = await models.complete_text(model, load_prompt("evaluator"), _grade_input(record, case, task), max_tokens=4000)
    return parse_verdict(text)


_RECOMMEND_SYSTEM = (
    "You compare two AI support agents, a teacher and a student, that answered the same cases "
    "and were graded by the same judge. Write one paragraph (at most 150 words) for the person "
    "who will rewrite the student's prompt: which cases the student lost points on, the recurring "
    "patterns behind it (not case-specific answers), and what the teacher did differently. Plain "
    "text, no lists, no headings."
)


async def recommend(teacher_results: list[Graded], student_results: list[Graded], *, model: str) -> str:
    """Summarise per-case teacher vs student scores and why the student is behind."""
    student_by_case = {g.record.case_id: g for g in student_results}
    lines: list[str] = []
    for t in sorted(teacher_results, key=lambda g: g.record.case_id):
        s = student_by_case.get(t.record.case_id)
        if s is None:
            raise ValueError(f"no student result for case '{t.record.case_id}'")
        lines.append(
            f"### case {t.record.case_id}\n"
            f"teacher score {t.verdict.score:.2f}: {t.verdict.reason}\n"
            f"student score {s.verdict.score:.2f}: {s.verdict.reason}\n"
        )
    text = await models.complete_text(model, _RECOMMEND_SYSTEM, "\n".join(lines), max_tokens=4000)
    if not text.strip():
        raise JudgeOutputError("empty recommendation from judge")
    return text.strip()
