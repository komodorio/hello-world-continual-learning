import pytest

from prompt_coach.roles import coach
from prompt_coach.roles.coach import CoachOutputError, parse_proposal, select_failures
from prompt_coach.types import Graded, PromptVersion, Record, Task, Verdict
from tests.conftest import FakeModel


def graded(agent: str, case_id: str, score: float) -> Graded:
    return Graded(
        record=Record(agent=agent, model="m", prompt_version=1, case_id=case_id, reply=f"{agent} reply"),  # type: ignore[arg-type]
        verdict=Verdict(score=score, reason=f"{agent} reason for {case_id}"),
    )


def test_select_failures_keeps_only_student_runs_below_threshold() -> None:
    runs = [
        graded("teacher", "refund", 0.2),  # teacher never goes to the coach
        graded("student", "refund", 0.69),
        graded("student", "compensation", 0.7),  # at threshold: not a failure
        graded("student", "two-questions", 0.95),
    ]
    assert [g.record.case_id for g in select_failures(runs, 0.7)] == ["refund"]


def test_parse_proposal_bumps_version_and_keeps_changelog() -> None:
    pv = parse_proposal('{"prompt": "Be brief.", "changelog": "shorter"}', current_version=3)
    assert pv == PromptVersion(version=4, text="Be brief.", changelog="shorter")


@pytest.mark.parametrize("garbage", ["not json", '{"prompt": ""}', '{"prompt": "x"}', '{"changelog": "y"}'])
def test_parse_proposal_fails_loudly(garbage: str) -> None:
    with pytest.raises(CoachOutputError):
        parse_proposal(garbage, current_version=1)


async def test_propose_returns_next_version_and_never_sees_expected(fake: FakeModel, support_task: Task) -> None:
    current = PromptVersion(version=1, text=support_task.initial_prompt)
    failures = [graded("student", c.id, 0.4) for c in support_task.cases]
    proposal = await coach.propose(
        support_task.initial_prompt,
        current,
        failures,
        "the student runs long",
        case_inputs={c.id: c.input for c in support_task.cases},
        history=["v1 mean 0.40 (teacher 0.90): initial prompt"],
        model="fake/coach",
    )
    assert proposal.version == 2
    assert proposal.changelog
    assert proposal.text != current.text

    coach_call = fake.calls[-1]
    assert coach_call["model"] == "fake/coach"
    sent = coach_call["system"] + coach_call["user"]
    for case in support_task.cases:
        assert case.input.strip() in sent, "the coach should see what the student saw"
        # Any single checklist line from `expected` leaking would defeat the design.
        for line in case.expected.splitlines():
            line = line.strip().lstrip("- ")
            if len(line) > 25:
                assert line not in sent, f"expected leaked to coach: {line!r}"
    assert "student reply" in sent and "student reason" in sent
    assert "v1 mean 0.40" in sent
