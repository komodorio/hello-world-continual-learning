from pathlib import Path

import pytest

from prompt_coach.task import load_task
from tests.conftest import SUPPORT_TASK


def test_loads_the_four_support_cases() -> None:
    task = load_task(SUPPORT_TASK)
    assert task.name == "support"
    assert [c.id for c in task.cases] == ["compensation", "missing-feature", "multi-request", "refund"]
    assert "Output format:" in task.initial_prompt
    for case in task.cases:
        assert "Policy snippet:" in case.input
        assert "Must not:" in case.expected


def test_case_filter_keeps_requested_cases_in_order() -> None:
    task = load_task(SUPPORT_TASK, ["refund", "compensation"])
    assert [c.id for c in task.cases] == ["refund", "compensation"]


def test_case_filter_rejects_unknown_id() -> None:
    with pytest.raises(ValueError, match="unknown case id"):
        load_task(SUPPORT_TASK, ["refund", "nope"])


def test_case_filter_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_task(SUPPORT_TASK, ["refund", "refund"])


def test_load_config_resolves_paths_against_config_dir(tmp_path: Path) -> None:
    from prompt_coach.config import load_config

    sub = tmp_path / "elsewhere"
    sub.mkdir()
    (sub / "config.yaml").write_text(
        "models: {teacher: a, student: b, evaluator: c, coach: d}\ntask: my-task\nruns_dir: out\n"
    )
    cfg = load_config(sub / "config.yaml", env_file=tmp_path / "no-such.env")
    assert cfg.task == sub.resolve() / "my-task"
    assert cfg.runs_dir == sub.resolve() / "out"


@pytest.mark.parametrize("missing", ["input", "expected"])
def test_rejects_case_missing_required_field(tmp_path: Path, missing: str) -> None:
    (tmp_path / "task.yaml").write_text("name: t\nprompt: do the thing\n")
    cases = tmp_path / "cases"
    cases.mkdir()
    fields = {"input": "hello", "expected": "must say hi"}
    del fields[missing]
    (cases / "a.yaml").write_text("".join(f"{k}: {v}\n" for k, v in fields.items()))
    with pytest.raises(ValueError, match=missing):
        load_task(tmp_path)


def test_rejects_task_without_prompt_or_cases(tmp_path: Path) -> None:
    (tmp_path / "task.yaml").write_text("name: t\n")
    with pytest.raises(ValueError, match="prompt"):
        load_task(tmp_path)
    (tmp_path / "task.yaml").write_text("name: t\nprompt: p\n")
    (tmp_path / "cases").mkdir()
    with pytest.raises(ValueError, match="no case files"):
        load_task(tmp_path)
