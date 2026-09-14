import asyncio
import json

import httpx
import pytest

from prompt_coach.config import Config
from prompt_coach.web.server import create_app
from tests.conftest import FakeModel


@pytest.fixture
async def client(config: Config):
    app = create_app(config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def read_sse(client: httpx.AsyncClient) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    async with client.stream("GET", "/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        name = ""
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
                if name == "end":
                    break
    return events


async def test_openapi_documents_every_route_and_the_event_schema(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert set(schema["paths"]) == {"/cases", "/start", "/test", "/events", "/runs", "/runs/{run_id}"}
    assert "text/event-stream" in schema["paths"]["/events"]["get"]["responses"]["200"]["content"]
    for name in ("Event", "RunRecord", "RoundResult", "Graded", "Verdict", "PromptVersion", "Case"):
        assert name in schema["components"]["schemas"], name
    assert (await client.get("/docs")).status_code == 200


async def test_index_and_cases(client: httpx.AsyncClient) -> None:
    assert (await client.get("/")).status_code == 200
    body = (await client.get("/cases")).json()
    assert body["task"] == "support"
    assert [c["id"] for c in body["cases"]] == ["compensation", "missing-feature", "multi-request", "refund"]
    assert body["models"]["student"] == "fake/student"
    assert "Output format:" in body["prompt"]


async def test_start_streams_events_and_saves_the_run(
    client: httpx.AsyncClient, fake: FakeModel, config: Config
) -> None:
    fake.student_scores = [{c.id: 0.9 for c in fake.cases}]  # within the gap from round one
    started = await client.post("/start", json={"cases": ["refund", "compensation"], "rounds": 3})
    assert started.status_code == 200
    run_id = started.json()["id"]
    assert started.json()["cases"] == ["refund", "compensation"]

    events = await read_sse(client)
    names = [n for n, _ in events]
    assert names[0] == "run_started" and names[-2:] == ["run_finished", "end"]
    assert names.count("round_finished") == 2
    assert names.count("graded") == 2 * 2 * 2  # 2 cases × 2 agents × 2 rounds
    finished = next(d for n, d in events if n == "run_finished")
    assert finished["run"]["id"] == run_id
    assert finished["run"]["case_ids"] == ["refund", "compensation"]

    runs = (await client.get("/runs")).json()
    assert [r["id"] for r in runs] == [run_id]
    assert runs[0]["student_means"] == [0.9, 0.9]
    detail = (await client.get(f"/runs/{run_id}")).json()
    assert len(detail["rounds"]) == 2 and detail["rounds"][0]["next_prompt"]["version"] == 2
    assert (await client.get("/runs/does-not-exist")).status_code == 404


async def test_start_rejects_unknown_case_and_concurrent_runs(client: httpx.AsyncClient, fake: FakeModel) -> None:
    assert (await client.post("/start", json={"cases": ["nope"]})).status_code == 400
    fake.student_scores = [{c.id: 0.5 for c in fake.cases}]
    assert (await client.post("/start", json={"cases": ["refund"], "rounds": 1})).status_code == 200
    assert (await client.post("/start", json={"cases": ["refund"], "rounds": 1})).status_code == 409
    await read_sse(client)  # let the first run finish before the loop is torn down


async def test_test_endpoint_runs_one_agent_and_grades(client: httpx.AsyncClient, fake: FakeModel) -> None:
    fake.student_scores = [{c.id: 0.33 for c in fake.cases}]
    response = await client.post("/test", json={"case": "refund", "agent": "student", "prompt": "[v1] custom prompt"})
    assert response.status_code == 200
    body = response.json()
    assert body["record"]["agent"] == "student" and body["record"]["case_id"] == "refund"
    assert body["verdict"]["score"] == 0.33
    student_call = next(c for c in fake.calls if c["model"] == "fake/student")
    assert "custom prompt" in student_call["system"]
    assert (await client.post("/test", json={"case": "nope"})).status_code == 400
    await asyncio.sleep(0)
