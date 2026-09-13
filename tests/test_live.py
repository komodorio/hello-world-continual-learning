"""The one test that spends tokens. Run with ``uv run pytest -m live``; skipped without a key."""

import os

import pytest
from dotenv import load_dotenv

from prompt_coach import evaluator
from prompt_coach.agent import Agent
from prompt_coach.config import load_config
from prompt_coach.task import load_task
from tests.conftest import REPO

load_dotenv(REPO / ".env")

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY is not set")
async def test_student_answers_and_judge_grades_one_case() -> None:
    config = load_config(REPO / "config.yaml", REPO / ".env")
    task = load_task(REPO / config.task, ["refund"])
    case = task.cases[0]
    record = await Agent("student", config.models.student, task.initial_prompt).run(case)
    assert record.reply and "Maya" in record.reply
    verdict = await evaluator.grade(record, case, task=task, model=config.models.evaluator)
    assert 0.0 <= verdict.score <= 1.0
    assert len(verdict.reason) > 20
