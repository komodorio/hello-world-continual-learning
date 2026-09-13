import json

from prompt_coach import loop
from prompt_coach.config import Config
from prompt_coach.types import Event, Task
from tests.conftest import FakeModel


async def collect(config: Config, task: Task) -> list[Event]:
    return [event async for event in loop.run_loop(config, task, run_id="test-run")]


def rounds_of(events: list[Event]) -> list[int]:
    return [e.round for e in events if e.type == "round_finished"]


async def test_stops_when_the_gap_closes(fake: FakeModel, config: Config, support_task: Task) -> None:
    ids = [c.id for c in support_task.cases]
    fake.teacher_scores = {i: 0.9 for i in ids}
    fake.student_scores = [{i: 0.5 for i in ids}, {i: 0.85 for i in ids}]  # v1 → v2
    events = await collect(config, support_task)
    assert rounds_of(events) == [1, 2]
    finished = events[-1]
    assert finished.type == "run_finished" and finished.run is not None
    assert finished.run.stop_reason.startswith("gap closed")
    assert [r.student.mean for r in finished.run.rounds] == [0.5, 0.85]
    assert finished.run.rounds[0].next_prompt is not None and finished.run.rounds[0].next_prompt.version == 2
    assert finished.run.rounds[1].next_prompt is None, "no coaching after the last round"


async def test_stops_at_max_rounds(fake: FakeModel, config: Config, support_task: Task) -> None:
    config.loop.max_rounds = 3
    fake.student_scores = [{c.id: 0.5 for c in support_task.cases}]  # never improves
    events = await collect(config, support_task)
    assert rounds_of(events) == [1, 2, 3]
    run = events[-1].run
    assert run is not None and run.stop_reason.startswith("round budget")
    assert [r.student_prompt.version for r in run.rounds] == [1, 2, 3]
    assert run.rounds[-1].next_prompt is None


async def test_teacher_prompt_never_changes_and_student_prompt_does(fake: FakeModel, config: Config, support_task: Task) -> None:
    config.loop.max_rounds = 3
    fake.student_scores = [{c.id: 0.5 for c in support_task.cases}]
    await collect(config, support_task)
    teacher_prompts = {c["system"] for c in fake.calls if c["model"] == "fake/teacher"}
    student_prompts = {c["system"] for c in fake.calls if c["model"] == "fake/student"}
    # ADK appends its own agent-identity line, so compare the prefix.
    assert len(teacher_prompts) == 1 and teacher_prompts.pop().startswith(support_task.initial_prompt)
    assert len(student_prompts) == 3


async def test_event_order(fake: FakeModel, config: Config, support_task: Task) -> None:
    fake.student_scores = [{c.id: 0.5 for c in support_task.cases}, {c.id: 0.9 for c in support_task.cases}]
    events = await collect(config, support_task)
    n = len(support_task.cases) * 2
    expected = (
        ["run_started"]
        + ["round_started"] + ["graded"] * n + ["round_finished", "prompt_proposed"]
        + ["round_started"] + ["graded"] * n + ["round_finished"]
        + ["run_finished"]
    )
    assert [e.type for e in events] == expected
    first_round_graded = [e.graded for e in events if e.type == "graded" and e.round == 1]
    assert [(g.record.case_id, g.record.agent) for g in first_round_graded if g] == [
        (c.id, a) for c in support_task.cases for a in ("teacher", "student")
    ]


async def test_coach_only_gets_failures(fake: FakeModel, config: Config, support_task: Task) -> None:
    ids = [c.id for c in support_task.cases]
    fake.student_scores = [{ids[0]: 0.3, ids[1]: 0.95, ids[2]: 0.5, ids[3]: 0.9}, {i: 0.9 for i in ids}]
    await collect(config, support_task)
    coach_input = next(c["user"] for c in fake.calls if c["model"] == "fake/coach")
    assert f"### case {ids[0]}" in coach_input and f"### case {ids[2]}" in coach_input
    assert f"### case {ids[1]}" not in coach_input and f"### case {ids[3]}" not in coach_input


async def test_run_file_is_written_and_replays_to_the_same_events(fake: FakeModel, config: Config, support_task: Task) -> None:
    fake.student_scores = [{c.id: 0.5 for c in support_task.cases}, {c.id: 0.9 for c in support_task.cases}]
    live = await collect(config, support_task)
    path = config.runs_dir / "test-run.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["id"] == "test-run" and len(data["rounds"]) == 2
    assert data["rounds"][0]["teacher"]["mean"] == 0.9

    replayed = list(loop.replay(loop.load_run(path)))
    assert [e.to_json() for e in replayed] == [e.to_json() for e in live]
