---
name: prompt-coach
description: >-
  Run, configure and extend prompt-coach, the teacher/student/evaluator/coach loop in this repo.
  Use when asked to start the web UI or a run, set API keys, change a model (Anthropic, Baseten,
  OpenRouter, any LiteLLM string), add a task or a case, inspect or replay a saved run under runs/,
  test one prompt on one case, or run the tests and linter.
---

# prompt-coach

A cheap model (student) learns a support job from an expensive one (teacher). Only the student's
prompt changes between rounds; an evaluator scores replies 0–1 and a coach rewrites the prompt.
Everything runs through `uv run prompt-coach <command>` from the repo root.

## Before anything that calls a model

1. `uv sync` once.
2. A provider key must be present for every provider used under `models:` in `config.yaml`.
   The provider is the prefix of the model string (`anthropic/…` → `ANTHROPIC_API_KEY`,
   `baseten/…` → `BASETEN_API_KEY`, `openrouter/…` → `OPENROUTER_API_KEY`).
   Put it in `.env` at the repo root (`cp .env.example .env`, git-ignored) or `export` it; the
   exported variable wins. With the default config only `ANTHROPIC_API_KEY` is needed.
3. Never print, commit or echo the contents of `.env`.

Commands that call models cost money: `run` is ~18 calls per round, up to `max_rounds` rounds.
`cases` and `replay` are free. Prefer `test` (2 calls) when checking one prompt or one model.

## Common tasks

**Start the web UI**: `uv run prompt-coach serve` → http://127.0.0.1:8000. In the *New run* form,
choose tickets, set `rounds` and `gap`, press *Start run*. Saved runs list under the form.
`--port`/`--host` to change the bind address. OpenAPI at `/docs`.

**Run the loop in the terminal**: `uv run prompt-coach run`. Subset and budget:
`--case refund --case compensation --rounds 3 --gap 0.05`.

**See the tickets and what the evaluator grades against**: `uv run prompt-coach cases` (`--full`
adds the customer message and policy snippet).

**Try one prompt on one ticket, no loop**:
`uv run prompt-coach test --case refund [--agent teacher|student] [--prompt file.md] [--model <litellm string>]`.

**Replay or inspect a saved run** (no model calls):
`uv run prompt-coach replay runs/<id>.json`, or one cell with full replies and reasons:
`--round 2 --case refund`. The JSON itself is a `RunRecord` (see reference.md).

**Change a model**: edit the string under `models:` in `config.yaml`, add the provider's key to
`.env`. Keep `anthropic/claude-haiku-4-5-20251001` as the default student unless asked otherwise.
Baseten example (student only, other roles stay on Anthropic):

```yaml
student: baseten/moonshotai/Kimi-K2.5     # needs BASETEN_API_KEY in .env
```

The slug after `baseten/` is Baseten's catalog id; list them with
`curl -s https://inference.baseten.co/v1/models -H "Authorization: Bearer $BASETEN_API_KEY"`.
An 8-character slug is treated as a dedicated deployment id.

**Add a task or a case**: copy `tasks/support/` to `tasks/<name>/`, edit `task.yaml` and add one
YAML per case in `cases/` with `id`, `trap`, `input`, `expected`. Point `task:` in `config.yaml`
at the folder (or pass `--task tasks/<name>`). Run `uv run prompt-coach cases --task tasks/<name>`
to check it loads before spending anything. Field meanings are in reference.md.

**Tests and lint**: `uv run pytest` (offline, fake model, ~1 s), `uv run ruff check src tests`,
`uv run ruff format --check src tests`. `uv run pytest -m live` makes two real model calls and
needs a key. CI runs the offline set on Python 3.11 and 3.12.

## Where things live

| Path | What |
|---|---|
| `config.yaml` | models, task folder, runs dir, loop settings (`max_rounds`, `min_rounds`, `gap`, `coach_threshold`) |
| `tasks/support/` | the example task: `task.yaml` + `cases/*.yaml` |
| `src/prompt_coach/loop.py` | the round loop and the stop rule; yields `Event`s |
| `src/prompt_coach/roles/` | `agent.py` (ADK + LiteLLM), `evaluator.py`, `coach.py` |
| `src/prompt_coach/prompts/` | the evaluator's and coach's system prompts (markdown) |
| `src/prompt_coach/llm.py` | the one function that calls LiteLLM; tests fake it |
| `src/prompt_coach/cli.py` | Typer commands and the terminal reporter |
| `src/prompt_coach/web/` | FastAPI server (SSE) and the single-page UI in `static/index.html` |
| `runs/` | one JSON per run, git-ignored |

For CLI flags, config fields, case YAML fields, HTTP endpoints and the run JSON shape, read
[reference.md](reference.md).
