# prompt-coach reference

## CLI

All commands take `--config <path>` (default `config.yaml`). `task`, `runs_dir` and `.env` resolve
relative to the config file's folder.

| Command | Options | Model calls |
|---|---|---|
| `run` | `--task DIR`, `--case ID` (repeatable), `--rounds N`, `--gap F`, `--quiet` | ~(2 agents + 2 grades) × cases + 2 per round |
| `test` | `--case ID` (required), `--agent teacher\|student`, `--prompt FILE`, `--model STR`, `--task DIR` | 2 |
| `cases` | `--task DIR`, `--case ID`, `--full` | 0 |
| `replay FILE` | `--quiet`, `--round N --case ID` (drill into one cell) | 0 |
| `serve` | `--port 8000`, `--host 127.0.0.1` | per run started from the page |

## config.yaml

```yaml
models:                    # LiteLLM strings; key for each provider comes from .env / environment
  teacher: anthropic/claude-sonnet-5
  student: anthropic/claude-haiku-4-5-20251001
  evaluator: anthropic/claude-sonnet-5
  coach: anthropic/claude-sonnet-5
task: tasks/support        # folder with task.yaml + cases/*.yaml
runs_dir: runs             # <runs_dir>/<id>.json, id = UTC timestamp + 4 hex chars
loop:
  max_rounds: 5            # hard budget
  min_rounds: 2            # the gap cannot close before this many rounds
  gap: 0.1                 # stop when student mean >= (teacher mean over all rounds so far) - gap
  coach_threshold: 0.7     # student replies scored below this are shown to the coach
```

Stop rule (`loop.stop_reason`): the baseline is the teacher's average over every round so far, not
the current round. Stop reasons are `gap closed: …` or `round budget spent (N)`; an exception
produces `error in round N: …` and the run is still saved.

Evaluator and coach calls are capped at `max_tokens` 4000 and 6000. A reasoning model in those
roles can hit the cap; `llm.text_of` raises with a message that says so.

## Task folder

```
tasks/<name>/
  task.yaml
  cases/<case-id>.yaml     # one per case; sorted by filename
```

`task.yaml`:

| Field | Required | Meaning |
|---|---|---|
| `prompt` | yes | prompt v1; both agents start from it, only the student's copy is rewritten |
| `name` | no | defaults to the folder name |
| `description` | no | shown in the UI's `/cases` payload |
| `output_format` | no | appended to the prompt and used by the evaluator for format checks |
| `grading_guidance` | no | extra rules for the evaluator (e.g. score caps for wrong decisions) |

`cases/*.yaml`:

| Field | Required | Meaning |
|---|---|---|
| `input` | yes | everything the agent sees; for the support task, customer message + policy snippet |
| `expected` | yes | the evaluator's checklist (must say / must not / must answer); the coach never sees it |
| `id` | no | defaults to the filename stem; must be unique within the task |
| `trap` | no | one line: what a weak agent tends to get wrong; shown in the CLI and UI |

## Run JSON (`RunRecord`)

```
id, started_at, task, case_ids, models{teacher,student,evaluator,coach}, gap, max_rounds,
teacher_prompt, stop_reason,
rounds[]: round, student_prompt{version,text,changelog}, recommendation, next_prompt,
          teacher{agent,scores{case_id: score},mean}, student{…},
          graded[]: record{agent,model,prompt_version,case_id,reply,latency_s}, verdict{score,reason}
```

`RunRecord.best_round` is the round with the highest student mean.

## HTTP API (`prompt-coach serve`)

| Method, path | Purpose |
|---|---|
| `GET /` | the single-page UI |
| `GET /cases` | task name, description, prompt v1, configured models and loop settings, cases |
| `POST /start` | `{cases?, rounds? (≤10), gap?}` → `{id, cases}`; 409 if a run is in flight |
| `GET /events` | SSE stream of the current run; late subscribers get the buffered history first |
| `POST /test` | `{case, agent?, prompt?, model?}` → `{record, verdict}`; model must be one of the configured ones |
| `GET /runs` | saved run summaries, newest first |
| `GET /runs/{id}` | full `RunRecord` |
| `GET /docs` | Swagger; `Event` schema is included under `components.schemas.Event` |

Event types: `run_started`, `round_started`, `graded`, `round_finished`, `prompt_proposed`,
`run_finished`, `error`, `log`. The server holds one run at a time.

## Tests

`tests/conftest.py` replaces `prompt_coach.llm.complete` with a fake, so `uv run pytest` is
offline. `tests/test_live.py` is marked `live` and deselected by default (`addopts` in
`pyproject.toml`); run it with `uv run pytest -m live`.
