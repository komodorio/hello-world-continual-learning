"""FastAPI backend: runs the loop as a background task and streams its Events over SSE."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from prompt_coach import evaluator, loop
from prompt_coach.agent import Agent
from prompt_coach.config import Config
from prompt_coach.task import load_task
from prompt_coach.types import AgentName, Event, Task

log = logging.getLogger("prompt_coach.server")
STATIC_DIR = Path(__file__).parent / "static"


MAX_ROUNDS = 10  # every round costs real tokens; the page must not be able to ask for 1000


class StartRequest(BaseModel):
    cases: list[str] | None = None
    rounds: int | None = Field(default=None, ge=1, le=MAX_ROUNDS)
    gap: float | None = Field(default=None, ge=0.0, le=1.0)


class TestRequest(BaseModel):
    case: str
    agent: AgentName = "student"
    prompt: str | None = None
    model: str | None = None


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


def _run_summary(run_path: Path) -> dict[str, Any]:
    run = loop.load_run(run_path)
    return {
        "id": run.id,
        "started_at": run.started_at,
        "task": run.task,
        "case_ids": run.case_ids,
        "rounds": len(run.rounds),
        "stop_reason": run.stop_reason,
        "teacher_means": [r.teacher.mean for r in run.rounds],
        "student_means": [r.student.mean for r in run.rounds],
    }


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="prompt-coach")
    state = RunState()

    def task_for(case_ids: list[str] | None) -> Task:
        try:
            return load_task(config.task, case_ids)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/cases")
    async def cases() -> dict[str, Any]:
        task = task_for(None)
        return {
            "task": task.name,
            "description": task.description,
            "prompt": task.initial_prompt,
            "models": config.models.model_dump(),
            "loop": config.loop.model_dump(),
            "cases": [c.model_dump() for c in task.cases],
        }

    async def run_in_background(run_config: Config, task: Task, run_id: str) -> None:
        try:
            async for event in loop.run_loop(run_config, task, run_id=run_id):
                state.publish(event)
        except Exception:
            log.exception("run %s failed", run_id)
        finally:
            state.finish()

    @app.post("/start")
    async def start(request: StartRequest) -> dict[str, Any]:
        if state.running:
            raise HTTPException(status_code=409, detail="a run is already in progress")
        task = task_for(request.cases)
        run_config = config.model_copy(deep=True)
        if request.rounds is not None:
            run_config.loop.max_rounds = request.rounds
        if request.gap is not None:
            run_config.loop.gap = request.gap
        run_id = loop.new_run_id()
        state.events.clear()
        state.task = asyncio.create_task(run_in_background(run_config, task, run_id))
        return {"id": run_id, "cases": [c.id for c in task.cases]}

    @app.post("/test")
    async def test(request: TestRequest) -> dict[str, Any]:
        task = task_for([request.case])
        case = task.cases[0]
        prompt = request.prompt.strip() if request.prompt and request.prompt.strip() else task.initial_prompt
        model = request.model or getattr(config.models, request.agent)
        if model not in config.models.model_dump().values():
            raise HTTPException(status_code=400, detail=f"model '{model}' is not one of the configured models")
        record = await Agent(request.agent, model, prompt).run(case)
        verdict = await evaluator.grade(record, case, task=task, model=config.models.evaluator)
        return {"record": record.model_dump(), "verdict": verdict.model_dump()}

    @app.get("/events")
    async def events() -> StreamingResponse:
        queue = state.subscribe()

        async def stream() -> AsyncIterator[str]:
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        yield "event: end\ndata: {}\n\n"
                        return
                    yield _sse(event)
            finally:
                state.subscribers.discard(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/runs")
    async def runs() -> list[dict[str, Any]]:
        if not config.runs_dir.exists():
            return []
        return [_run_summary(p) for p in sorted(config.runs_dir.glob("*.json"), reverse=True)]

    @app.get("/runs/{run_id}")
    async def run_detail(run_id: str) -> dict[str, Any]:
        path = config.runs_dir / f"{run_id}.json"
        if not path.is_file() or path.resolve().parent != config.runs_dir.resolve():
            raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
        return loop.load_run(path).model_dump(mode="json")

    return app
