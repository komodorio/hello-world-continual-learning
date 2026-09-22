"""Saved runs: one JSON file per run under runs/, and a token-free replay of its events."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.types import Event, RunRecord


def new_run_id() -> str:
    """Sortable timestamp plus a short random suffix so two runs in the same second never collide."""
    return f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def save_run(run: RunRecord, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run.id}.json"
    path.write_text(run.model_dump_json(indent=2))
    return path


def load_run(path: Path | str) -> RunRecord:
    return RunRecord.model_validate_json(Path(path).read_text())


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
