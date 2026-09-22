"""Load config.yaml and .env into one validated Config object."""

from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Models(BaseModel):
    teacher: str
    student: str
    evaluator: str
    coach: str


class LoopSettings(BaseModel):
    max_rounds: int = Field(default=5, ge=1)
    # The gap can only close once the teacher baseline is an average of at least this many rounds;
    # a single weak teacher round would otherwise end the loop before any coaching happened.
    min_rounds: int = Field(default=2, ge=1)
    gap: float = Field(default=0.1, ge=0.0, le=1.0)
    coach_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class Config(BaseModel):
    models: Models
    task: Path = Path("tasks/support")
    runs_dir: Path = Path("runs")
    loop: LoopSettings = LoopSettings()


def load_config(path: Path | str = "config.yaml", env_file: Path | str | None = None) -> Config:
    """Read the YAML config and load API keys from the env file into the process env.

    Relative paths (``task``, ``runs_dir``, and the default ``.env``) resolve against the config
    file's directory, so ``--config elsewhere/config.yaml`` works from any working directory.
    Keys are never returned or printed; providers read them from ``os.environ``.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"{config_path} not found; copy config.example.yaml to {config_path}")
    base = config_path.resolve().parent
    load_dotenv(Path(env_file) if env_file is not None else base / ".env")
    with config_path.open() as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path} must contain a mapping at the top level")
    config = Config.model_validate(raw)
    if not config.task.is_absolute():
        config.task = base / config.task
    if not config.runs_dir.is_absolute():
        config.runs_dir = base / config.runs_dir
    return config
