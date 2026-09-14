import pytest

from prompt_coach.roles import evaluator
from prompt_coach.roles.evaluator import JudgeOutputError, parse_verdict
from prompt_coach.types import Graded, Record, Task, Verdict
from tests.conftest import FakeModel


def test_parse_verdict_reads_score_and_reason() -> None:
    v = parse_verdict('{"score": 0.65, "reason": "Missed the pickup option."}')
    assert v.score == 0.65
    assert v.reason == "Missed the pickup option."


@pytest.mark.parametrize(("raw", "expected"), [(1.4, 1.0), (-0.2, 0.0), (1, 1.0), (0, 0.0)])
def test_parse_verdict_clamps_to_unit_interval(raw: float, expected: float) -> None:
    assert parse_verdict(f'{{"score": {raw}, "reason": "r"}}').score == expected


def test_parse_verdict_tolerates_code_fences_and_trailing_text() -> None:
    assert parse_verdict('```json\n{"score": 0.5, "reason": "ok"}\n```').score == 0.5
    assert parse_verdict('{"score": 0.5, "reason": "ok"} — that is my verdict.').reason == "ok"


def test_parse_verdict_tolerates_one_missing_closing_brace() -> None:
    assert parse_verdict('{"score": 0.3, "reason": "cut off"').score == 0.3


@pytest.mark.parametrize(
    "garbage",
    [
        "I think this deserves a 7 out of 10.",
        '{"score": "high", "reason": "r"}',
        '{"score": true, "reason": "r"}',
        '{"score": 0.5}',
        '{"score": 0.5, "reason": ""}',
        "[0.5, 'r']",
        '{"score": 0.5, "reason": "cut off mid',
    ],
)
def test_parse_verdict_fails_loudly_on_garbage(garbage: str) -> None:
    with pytest.raises(JudgeOutputError):
        parse_verdict(garbage)


async def test_grade_uses_case_and_hides_agent_identity(fake: FakeModel, support_task: Task) -> None:
    case = support_task.cases[0]
    record = Record(agent="student", model="fake/student", prompt_version=1, case_id=case.id, reply="[student v1] hi")
    fake.student_scores = [{c.id: 0.42 for c in support_task.cases}]
    verdict = await evaluator.grade(record, case, task=support_task, model="fake/judge")
    assert verdict.score == 0.42
    judge_call = fake.calls[-1]
    assert case.expected.strip() in judge_call["user"]
    assert "## The agent's reply" in judge_call["user"]
    assert "student" not in judge_call["user"].split("## The agent's reply")[0]


async def test_grade_rejects_mismatched_case(fake: FakeModel, support_task: Task) -> None:
    record = Record(agent="student", model="m", prompt_version=1, case_id="refund", reply="x")
    with pytest.raises(ValueError, match="record is for case"):
        await evaluator.grade(record, support_task.cases[0], task=support_task, model="fake/judge")


async def test_grade_retries_then_raises_on_garbage_with_the_evidence(fake: FakeModel, support_task: Task) -> None:
    case = support_task.cases[0]
    record = Record(agent="student", model="m", prompt_version=1, case_id=case.id, reply="[student v1] hi")
    fake.judge_text = "no json here"
    with pytest.raises(JudgeOutputError, match=r"finish_reason='stop'.*12 chars"):
        await evaluator.grade(record, case, task=support_task, model="fake/judge")
    assert len([c for c in fake.calls if c["model"] == "fake/judge"]) == 3


async def test_recommend_pairs_teacher_and_student_per_case(fake: FakeModel, support_task: Task) -> None:
    def graded(agent: str, case_id: str, score: float) -> Graded:
        return Graded(
            record=Record(agent=agent, model="m", prompt_version=1, case_id=case_id, reply="r"),  # type: ignore[arg-type]
            verdict=Verdict(score=score, reason=f"{agent} reason"),
        )

    teacher = [graded("teacher", "refund", 0.9)]
    student = [graded("student", "refund", 0.4)]
    text = await evaluator.recommend(teacher, student, model="fake/judge")
    assert text == fake.recommendation
    assert "teacher score 0.90" in fake.calls[-1]["user"]
    assert "student score 0.40" in fake.calls[-1]["user"]
    with pytest.raises(ValueError, match="no student result"):
        await evaluator.recommend(teacher, [], model="fake/judge")
