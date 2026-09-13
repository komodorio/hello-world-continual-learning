"""Data types shared by every module. Plain pydantic models, no behaviour beyond validation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

AgentName = Literal["teacher", "student"]


class Case(BaseModel):
    """One task instance: what the agent sees and what a perfect reply must satisfy."""

    id: str
    input: str
    expected: str


class Task(BaseModel):
    """A task folder: the shared v1 prompt, the format rules, grading hints, and the cases."""

    name: str
    description: str = ""
    prompt: str
    output_format: str = ""
    grading_guidance: str = ""
    cases: list[Case]

    @property
    def initial_prompt(self) -> str:
        """Prompt v1 for both agents: the task prompt plus the output format rules."""
        if not self.output_format.strip():
            return self.prompt.strip()
        return f"{self.prompt.strip()}\n\nOutput format:\n{self.output_format.strip()}"


class PromptVersion(BaseModel):
    version: int = Field(ge=1)
    text: str
    changelog: str = "initial prompt"


class Record(BaseModel):
    """One agent reply to one case."""

    agent: AgentName
    model: str
    prompt_version: int
    case_id: str
    reply: str
    latency_s: float = 0.0


class Verdict(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reason: str


class Graded(BaseModel):
    record: Record
    verdict: Verdict


class Scorecard(BaseModel):
    agent: AgentName
    scores: dict[str, float]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mean(self) -> float:
        if not self.scores:
            return 0.0
        return sum(self.scores.values()) / len(self.scores)


class RoundResult(BaseModel):
    round: int = Field(ge=1)
    student_prompt: PromptVersion
    graded: list[Graded]
    teacher: Scorecard
    student: Scorecard
    recommendation: str = ""
    next_prompt: PromptVersion | None = None

    @model_validator(mode="after")
    def _prompt_version_matches(self) -> RoundResult:
        if self.next_prompt is not None and self.next_prompt.version != self.student_prompt.version + 1:
            raise ValueError("next_prompt must be exactly one version after student_prompt")
        return self


class RunRecord(BaseModel):
    """Everything persisted to runs/<ts>.json; enough to replay a run without tokens."""

    id: str
    started_at: str
    task: str
    case_ids: list[str]
    models: dict[str, str]
    gap: float
    max_rounds: int
    teacher_prompt: str
    rounds: list[RoundResult] = Field(default_factory=list)
    stop_reason: str = ""


EventType = Literal[
    "run_started",
    "round_started",
    "graded",
    "round_finished",
    "prompt_proposed",
    "run_finished",
    "log",
    "error",
]


class Event(BaseModel):
    """What the loop yields. The CLI renders these; the server streams them over SSE."""

    type: EventType
    round: int = 0
    message: str = ""
    graded: Graded | None = None
    prompt: PromptVersion | None = None
    result: RoundResult | None = None
    run: RunRecord | None = None

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
