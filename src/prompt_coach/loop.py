"""The teacher/student/evaluator/coach loop. Yields Events; the CLI and the web UI render them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from prompt_coach.config import Config
from prompt_coach.roles import coach, evaluator
from prompt_coach.roles.agent import Agent
from prompt_coach.runs import new_run_id, save_run
from prompt_coach.types import (
    AgentName,
    Case,
    Event,
    Graded,
    PromptVersion,
    RoundResult,
    RunRecord,
    Scorecard,
    Task,
)


async def _run_and_grade(agent: Agent, case: Case, task: Task, judge_model: str) -> Graded:
    record = await agent.run(case)
    verdict = await evaluator.grade(record, case, task=task, model=judge_model)
    return Graded(record=record, verdict=verdict)


def _scorecard(graded: list[Graded], agent: AgentName) -> Scorecard:
    return Scorecard(agent=agent, scores={g.record.case_id: g.verdict.score for g in graded if g.record.agent == agent})


def _sorted(graded: list[Graded], cases: list[Case]) -> list[Graded]:
    """Deterministic order: case order, teacher before student. Replays match live runs."""
    order = {c.id: i for i, c in enumerate(cases)}
    return sorted(graded, key=lambda g: (order[g.record.case_id], g.record.agent != "teacher"))


def stop_reason(
    result: RoundResult, gap: float, max_rounds: int, teacher_baseline: float | None = None, min_rounds: int = 1
) -> str:
    """Empty string means keep going.

    ``teacher_baseline`` is the teacher's mean averaged over all rounds so far; using it instead of
    this round's teacher mean stops one lucky or unlucky teacher round from ending the loop.
    ``min_rounds`` keeps the gap from closing before that average has enough samples.
    """
    baseline = result.teacher.mean if teacher_baseline is None else teacher_baseline
    if result.round >= min_rounds and result.student.mean >= baseline - gap:
        return f"gap closed: student {result.student.mean:.2f} within {gap:.2f} of teacher {baseline:.2f}"
    if result.round >= max_rounds:
        return f"round budget spent ({max_rounds})"
    return ""


async def run_loop(config: Config, task: Task, *, run_id: str | None = None) -> AsyncIterator[Event]:
    """Run rounds until the gap closes or the budget is spent; persist after every round."""
    settings = config.loop
    teacher_prompt = task.initial_prompt
    student_prompt = PromptVersion(version=1, text=teacher_prompt, changelog="initial prompt (same as teacher)")
    run = RunRecord(
        id=run_id or new_run_id(),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        task=task.name,
        case_ids=[c.id for c in task.cases],
        models=config.models.model_dump(),
        gap=settings.gap,
        max_rounds=settings.max_rounds,
        teacher_prompt=teacher_prompt,
    )
    case_inputs = {c.id: c.input for c in task.cases}
    yield Event(type="run_started", run=run.model_copy(deep=True))

    try:
        for round_no in range(1, settings.max_rounds + 1):
            yield Event(type="round_started", round=round_no, prompt=student_prompt)

            teacher = Agent("teacher", config.models.teacher, teacher_prompt, version=1)
            student = Agent("student", config.models.student, student_prompt.text, version=student_prompt.version)
            graded = _sorted(
                list(
                    await asyncio.gather(
                        *[
                            _run_and_grade(a, c, task, config.models.evaluator)
                            for c in task.cases
                            for a in (teacher, student)
                        ]
                    )
                ),
                task.cases,
            )
            for g in graded:
                yield Event(type="graded", round=round_no, graded=g)

            teacher_card = _scorecard(graded, "teacher")
            student_card = _scorecard(graded, "student")
            recommendation = await evaluator.recommend(
                [g for g in graded if g.record.agent == "teacher"],
                [g for g in graded if g.record.agent == "student"],
                model=config.models.evaluator,
            )
            result = RoundResult(
                round=round_no,
                student_prompt=student_prompt,
                graded=graded,
                teacher=teacher_card,
                student=student_card,
                recommendation=recommendation,
            )
            teacher_means = [r.teacher.mean for r in run.rounds] + [teacher_card.mean]
            reason = stop_reason(
                result,
                settings.gap,
                settings.max_rounds,
                sum(teacher_means) / len(teacher_means),
                min_rounds=settings.min_rounds,
            )
            if not reason:
                failures = coach.select_failures(graded, settings.coach_threshold)
                history = [
                    f"v{r.student_prompt.version} mean {r.student.mean:.2f} (teacher {r.teacher.mean:.2f}): "
                    f"{r.student_prompt.changelog}"
                    for r in [*run.rounds, result]
                ]
                result.next_prompt = await coach.propose(
                    teacher_prompt,
                    student_prompt,
                    failures,
                    recommendation,
                    case_inputs=case_inputs,
                    history=history,
                    model=config.models.coach,
                )
            run.rounds.append(result)
            run.stop_reason = reason
            save_run(run, config.runs_dir)

            yield Event(type="round_finished", round=round_no, result=result)
            if result.next_prompt is not None:
                yield Event(type="prompt_proposed", round=round_no, prompt=result.next_prompt)
                student_prompt = result.next_prompt
            if reason:
                break
    except Exception as exc:
        # Persist the failure so the run file is never silently half-written.
        run.stop_reason = f"error in round {len(run.rounds) + 1}: {type(exc).__name__}: {exc}"
        save_run(run, config.runs_dir)
        yield Event(type="error", round=len(run.rounds) + 1, message=run.stop_reason, run=run)
        raise

    yield Event(type="run_finished", message=run.stop_reason, run=run)
