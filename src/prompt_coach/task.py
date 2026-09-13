"""Load a task folder: tasks/<name>/task.yaml plus tasks/<name>/cases/*.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from prompt_coach.types import Case, Task


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return data


def load_case(path: Path) -> Case:
    data = _read_yaml(path)
    for field in ("input", "expected"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError(f"{path}: missing or empty '{field}'")
    return Case(id=str(data.get("id") or path.stem), input=data["input"], expected=data["expected"])


def load_task(path: Path | str, case_ids: list[str] | None = None) -> Task:
    """Load a task; ``case_ids`` (from ``--case``) keeps only those cases, in that order."""
    folder = Path(path)
    task_file = folder / "task.yaml"
    if not task_file.exists():
        raise FileNotFoundError(f"{task_file} not found")
    data = _read_yaml(task_file)
    if not isinstance(data.get("prompt"), str) or not data["prompt"].strip():
        raise ValueError(f"{task_file}: missing 'prompt'")

    cases = [load_case(p) for p in sorted((folder / "cases").glob("*.yaml"))]
    if not cases:
        raise ValueError(f"{folder / 'cases'}: no case files found")
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"duplicate case id '{case.id}' in {folder}")
        seen.add(case.id)

    if case_ids:
        if len(set(case_ids)) != len(case_ids):
            raise ValueError(f"duplicate case id(s) in selection: {case_ids}")
        by_id = {c.id: c for c in cases}
        unknown = [cid for cid in case_ids if cid not in by_id]
        if unknown:
            raise ValueError(f"unknown case id(s) {unknown}; available: {sorted(by_id)}")
        cases = [by_id[cid] for cid in case_ids]

    return Task(
        name=str(data.get("name") or folder.name),
        description=str(data.get("description") or ""),
        prompt=data["prompt"],
        output_format=str(data.get("output_format") or ""),
        grading_guidance=str(data.get("grading_guidance") or ""),
        cases=cases,
    )
