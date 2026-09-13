"""The teacher/student/evaluator/coach loop. Yields Events; the CLI and the server render them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from pathlib import Path

from prompt_coach import coach, evaluator
from prompt_coach.agent import Agent
from prompt_coach.config import Config
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


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


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


def stop_reason(result: RoundResult, gap: float, max_rounds: int) -> str:
    """Empty string means keep going."""
    if result.student.mean >= result.teacher.mean - gap:
        return f"gap closed: student {result.student.mean:.2f} within {gap:.2f} of teacher {result.teacher.mean:.2f}"
    if result.round >= max_rounds:
        return f"round budget spent ({max_rounds})"
    return ""


def save_run(run: RunRecord, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run.id}.json"
    path.write_text(run.model_dump_json(indent=2))
    return path


def load_run(path: Path | str) -> RunRecord:
    return RunRecord.model_validate_json(Path(path).read_text())


async def run_loop(config: Config, task: Task, *, run_id: str | None = None) -> AsyncIterator[Event]:
    """Run rounds until the gap closes or the budget is spent; persist after every round."""
    settings = config.loop
    teacher_prompt = task.initial_prompt
    student_prompt = PromptVersion(version=1, text=teacher_prompt, changelog="initial prompt (same as teacher)")
    run = RunRecord(
        id=run_id or _now_id(),
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
                        *[_run_and_grade(a, c, task, config.models.evaluator) for c in task.cases for a in (teacher, student)]
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
            reason = stop_reason(result, settings.gap, settings.max_rounds)
            if not reason:
                failures = coach.select_failures(graded, settings.coach_threshold)
                history = [
                    f"v{r.student_prompt.version} mean {r.student.mean:.2f} (teacher {r.teacher.mean:.2f}): {r.student_prompt.changelog}"
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
        yield Event(type="error", round=len(run.rounds) + 1, message=f"{type(exc).__name__}: {exc}")
        raise

    yield Event(type="run_finished", message=run.stop_reason, run=run)


def replay(run: RunRecord) -> Iterator[Event]:
    """Re-emit the events of a saved run in the same order as ``run_loop`` did, without tokens."""
    yield Event(type="run_started", run=run.model_copy(update={"rounds": [], "stop_reason": ""}, deep=True))
    for result in run.rounds:
        yield Event(type="round_started", round=result.round, prompt=result.student_prompt)
        for g in result.graded:
            yield Event(type="graded", round=result.round, graded=g)
        yield Event(type="round_finished", round=result.round, result=result)
        if result.next_prompt is not None:
            yield Event(type="prompt_proposed", round=result.round, prompt=result.next_prompt)
    yield Event(type="run_finished", message=run.stop_reason, run=run)
