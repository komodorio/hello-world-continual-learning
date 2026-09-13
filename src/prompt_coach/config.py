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
    gap: float = Field(default=0.1, ge=0.0, le=1.0)
    coach_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class Config(BaseModel):
    models: Models
    task: Path = Path("tasks/support")
    runs_dir: Path = Path("runs")
    loop: LoopSettings = LoopSettings()


def load_config(path: Path | str = "config.yaml", env_file: Path | str = ".env") -> Config:
    """Read the YAML config and load API keys from the env file into the process env.

    Keys are never returned or printed; providers read them from ``os.environ``.
    """
    load_dotenv(env_file)
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"{config_path} not found; copy config.example.yaml to {config_path}")
    with config_path.open() as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path} must contain a mapping at the top level")
    return Config.model_validate(raw)
