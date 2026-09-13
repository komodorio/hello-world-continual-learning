"""The evaluator and coach prompts (evaluator.md, coach.md) live in this folder; load_prompt reads them."""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt '{name}' not found at {path}")
    return path.read_text().strip()
