"""FastAPI backend: runs the loop as a background task and streams its Events over SSE."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from prompt_coach import loop, runs
from prompt_coach.config import Config
from prompt_coach.roles import evaluator
from prompt_coach.roles.agent import Agent
from prompt_coach.task import load_task
from prompt_coach.types import AgentName, Case, Event, Record, RunRecord, Task, Verdict

log = logging.getLogger("prompt_coach.server")
STATIC_DIR = Path(__file__).parent / "static"


MAX_ROUNDS = 10  # every round costs real tokens; the page must not be able to ask for 1000


class StartRequest(BaseModel):
    cases: list[str] | None = Field(default=None, description="Case ids to run; omit for all cases in the task.")
    rounds: int | None = Field(default=None, ge=1, le=MAX_ROUNDS, description="Round budget; default from config.")
    gap: float | None = Field(default=None, ge=0.0, le=1.0, description="Stop when student >= teacher - gap.")


class StartResponse(BaseModel):
    id: str = Field(description="Run id; also the file name under runs/.")
    cases: list[str]


class TestRequest(BaseModel):
    case: str = Field(description="Case id.")
    agent: AgentName = Field(default="student", description="Which configured agent to run.")
    prompt: str | None = Field(default=None, description="Prompt override; default is the task's v1 prompt.")
    model: str | None = Field(default=None, description="Model override; must be one of the configured models.")


class TestResponse(BaseModel):
    record: Record
    verdict: Verdict


class CasesResponse(BaseModel):
    task: str
    description: str
    prompt: str = Field(description="Prompt v1, shared by teacher and student on round 1.")
    models: dict[str, str]
    loop: dict[str, float]
    cases: list[Case]


class RunSummary(BaseModel):
    id: str
    started_at: str
    task: str
    case_ids: list[str]
    rounds: int
    stop_reason: str
    teacher_means: list[float]
    student_means: list[float]


class RunState:
    """The one run the server can have in flight, plus its event history for late subscribers."""

    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None
        self.events: list[Event] = []
        self.subscribers: set[asyncio.Queue[Event | None]] = set()

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    def finish(self) -> None:
        for queue in list(self.subscribers):
            queue.put_nowait(None)

    def subscribe(self) -> asyncio.Queue[Event | None]:
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        for event in self.events:
            queue.put_nowait(event)
        if not self.running:
            queue.put_nowait(None)
        self.subscribers.add(queue)
        return queue


def _sse(event: Event) -> str:
    return f"event: {event.type}\ndata: {json.dumps(event.to_json())}\n\n"


def _run_summary(run_path: Path) -> RunSummary:
    run = runs.load_run(run_path)
    return RunSummary(
        id=run.id,
        started_at=run.started_at,
        task=run.task,
        case_ids=run.case_ids,
        rounds=len(run.rounds),
        stop_reason=run.stop_reason,
        teacher_means=[r.teacher.mean for r in run.rounds],
        student_means=[r.student.mean for r in run.rounds],
    )


SSE_DOC = (
    "Server-Sent Events stream of the run in progress. Late subscribers first receive every buffered "
    "event of the current run, then live ones. Each SSE `event:` is one of `run_started`, `round_started`, "
    "`graded`, `round_finished`, `prompt_proposed`, `run_finished`, `error`, `log`, and its `data:` is the "
    "JSON of the `Event` model (see `components.schemas.Event`). A final `end` event closes the stream. "
    "A `: keep-alive` comment is sent every 15 s while idle."
)


def create_app(config: Config) -> FastAPI:
    app = FastAPI(
        title="prompt-coach",
        version="0.1.0",
        summary="A hello-world for continual learning of LLM agents.",
        description=(
            "A teacher (strong model) and a student (weak model) run the same agent on the same cases; "
            "an evaluator scores each reply 0-1 with a reason; a coach rewrites only the student's prompt "
            "until the student's mean is within `gap` of the teacher's. This API runs that loop and streams "
            "its events. Interactive docs: `/docs` (Swagger) and `/redoc`."
        ),
        openapi_tags=[
            {"name": "task", "description": "The task folder: cases, traps, prompt v1, configured models."},
            {"name": "runs", "description": "Start the loop, stream its events, browse and replay saved runs."},
            {"name": "test", "description": "One agent, one case, one verdict. No loop, no coach."},
        ],
    )
    state = RunState()

    def task_for(case_ids: list[str] | None) -> Task:
        try:
            return load_task(config.task, case_ids)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/cases", tags=["task"], summary="List cases, traps, prompt v1 and models", response_model=CasesResponse)
    async def cases() -> CasesResponse:
        task = task_for(None)
        return CasesResponse(
            task=task.name,
            description=task.description,
            prompt=task.initial_prompt,
            models=config.models.model_dump(),
            loop=config.loop.model_dump(),
            cases=task.cases,
        )

    async def run_in_background(run_config: Config, task: Task, run_id: str) -> None:
        try:
            async for event in loop.run_loop(run_config, task, run_id=run_id):
                state.publish(event)
        except Exception:
            log.exception("run %s failed", run_id)
        finally:
            state.finish()

    @app.post(
        "/start",
        tags=["runs"],
        summary="Start the teacher/student/evaluator/coach loop in the background",
        response_model=StartResponse,
        responses={
            400: {"description": "Unknown or duplicate case id"},
            409: {"description": "A run is already in progress"},
        },
    )
    async def start(request: StartRequest) -> StartResponse:
        if state.running:
            raise HTTPException(status_code=409, detail="a run is already in progress")
        task = task_for(request.cases)
        run_config = config.model_copy(deep=True)
        if request.rounds is not None:
            run_config.loop.max_rounds = request.rounds
        if request.gap is not None:
            run_config.loop.gap = request.gap
        run_id = runs.new_run_id()
        state.events.clear()
        state.task = asyncio.create_task(run_in_background(run_config, task, run_id))
        return StartResponse(id=run_id, cases=[c.id for c in task.cases])

    @app.post(
        "/test",
        tags=["test"],
        summary="Run one agent on one case and grade it",
        response_model=TestResponse,
        responses={400: {"description": "Unknown case or model not in config"}},
    )
    async def test(request: TestRequest) -> TestResponse:
        task = task_for([request.case])
        case = task.cases[0]
        prompt = request.prompt.strip() if request.prompt and request.prompt.strip() else task.initial_prompt
        model = request.model or getattr(config.models, request.agent)
        if model not in config.models.model_dump().values():
            raise HTTPException(status_code=400, detail=f"model '{model}' is not one of the configured models")
        record = await Agent(request.agent, model, prompt).run(case)
        verdict = await evaluator.grade(record, case, task=task, model=config.models.evaluator)
        return TestResponse(record=record, verdict=verdict)

    @app.get(
        "/events",
        tags=["runs"],
        summary="Stream the current run's events (SSE)",
        description=SSE_DOC,
        response_class=StreamingResponse,
        responses={
            200: {
                "content": {"text/event-stream": {"schema": {"type": "string", "format": "sse"}}},
                "description": "text/event-stream of Event JSON",
            }
        },
    )
    async def events() -> StreamingResponse:
        queue = state.subscribe()

        async def stream() -> AsyncIterator[str]:
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        yield "event: end\ndata: {}\n\n"
                        return
                    yield _sse(event)
            finally:
                state.subscribers.discard(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/runs", tags=["runs"], summary="List saved runs, newest first", response_model=list[RunSummary])
    async def list_runs() -> list[RunSummary]:
        if not config.runs_dir.exists():
            return []
        return [_run_summary(p) for p in sorted(config.runs_dir.glob("*.json"), reverse=True)]

    @app.get(
        "/runs/{run_id}",
        tags=["runs"],
        summary="Full saved run: every round, reply, verdict and prompt version",
        response_model=RunRecord,
        responses={404: {"description": "No such run"}},
    )
    async def run_detail(run_id: str) -> RunRecord:
        path = config.runs_dir / f"{run_id}.json"
        if not path.is_file() or path.resolve().parent != config.runs_dir.resolve():
            raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
        return runs.load_run(path)

    def openapi_with_event_schema() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        event_schema = Event.model_json_schema(ref_template="#/components/schemas/{model}")
        defs = event_schema.pop("$defs", {})
        schema.setdefault("components", {}).setdefault("schemas", {}).update(defs)
        schema["components"]["schemas"]["Event"] = event_schema
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_with_event_schema  # type: ignore[method-assign]
    return app
