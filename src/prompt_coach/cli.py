"""Command line: run the loop, test one case, replay a saved run, serve the UI."""

from __future__ import annotations

import asyncio
import difflib
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from prompt_coach import evaluator, loop
from prompt_coach.agent import Agent
from prompt_coach.config import Config, load_config
from prompt_coach.task import load_task
from prompt_coach.types import AgentName, Event, RoundResult

app = typer.Typer(help="A hello-world for continual learning: a coach rewrites a weak agent's prompt until it catches up.")
console = Console()

ConfigOpt = Annotated[Path, typer.Option("--config", help="Path to config.yaml")]
CaseOpt = Annotated[Optional[list[str]], typer.Option("--case", help="Case id; repeatable")]


def _score_style(score: float) -> str:
    if score >= 0.8:
        return "green"
    if score >= 0.6:
        return "yellow"
    return "red"


def _fmt(score: float) -> Text:
    return Text(f"{score:.2f}", style=_score_style(score))


def prompt_diff(old: str, new: str) -> Text:
    """A coloured unified diff of two prompts."""
    lines = difflib.unified_diff(old.splitlines(), new.splitlines(), "previous", "proposed", lineterm="", n=1)
    text = Text()
    for line in lines:
        style = "green" if line.startswith("+") else "red" if line.startswith("-") else "cyan" if line.startswith("@@") else "dim"
        text.append(line + "\n", style=style)
    return text


def round_table(result: RoundResult) -> Table:
    table = Table(title=f"Round {result.round} · student prompt v{result.student_prompt.version}", show_footer=True)
    table.add_column("case", footer="mean")
    table.add_column("teacher", justify="right", footer=_fmt(result.teacher.mean))
    table.add_column("student", justify="right", footer=_fmt(result.student.mean))
    for case_id, t_score in result.teacher.scores.items():
        table.add_row(case_id, _fmt(t_score), _fmt(result.student.scores[case_id]))
    return table


class Reporter:
    """Renders the loop's events to the terminal. Stateless except for the previous prompt."""

    def __init__(self, console: Console, verbose: bool = True) -> None:
        self.console = console
        self.verbose = verbose
        self.previous_prompt = ""

    def handle(self, event: Event) -> None:
        if event.type == "run_started" and event.run:
            run = event.run
            self.console.print(
                Panel(
                    f"task [bold]{run.task}[/] · cases {', '.join(run.case_ids)}\n"
                    f"teacher [green]{run.models['teacher']}[/] · student [yellow]{run.models['student']}[/]\n"
                    f"evaluator {run.models['evaluator']} · coach {run.models['coach']}\n"
                    f"stop when student >= teacher - {run.gap} or after {run.max_rounds} rounds",
                    title=f"run {run.id}",
                    border_style="blue",
                )
            )
        elif event.type == "round_started" and event.prompt:
            self.previous_prompt = event.prompt.text
            self.console.rule(f"[bold]round {event.round}[/] · student prompt v{event.prompt.version}")
        elif event.type == "graded" and event.graded and self.verbose:
            g = event.graded
            self.console.print(
                f"  [dim]{g.record.case_id:16s}[/] {g.record.agent:8s}",
                _fmt(g.verdict.score),
                f"[dim]{g.verdict.reason[:110]}{'…' if len(g.verdict.reason) > 110 else ''}[/]",
            )
        elif event.type == "round_finished" and event.result:
            self.console.print(round_table(event.result))
            if event.result.recommendation:
                self.console.print(Panel(event.result.recommendation, title="evaluator's recommendation", border_style="dim"))
        elif event.type == "prompt_proposed" and event.prompt:
            self.console.print(
                Panel(
                    prompt_diff(self.previous_prompt, event.prompt.text),
                    title=f"student prompt v{event.prompt.version} · {event.prompt.changelog}",
                    border_style="magenta",
                )
            )
        elif event.type == "run_finished" and event.run:
            run = event.run
            if run.rounds:
                trend = " → ".join(f"{r.student.mean:.2f}" for r in run.rounds)
                teacher_mean = sum(r.teacher.mean for r in run.rounds) / len(run.rounds)
                self.console.print(
                    Panel(
                        f"teacher {teacher_mean:.2f} (avg over rounds) · student {trend}\n{run.stop_reason}",
                        title="result",
                        border_style="blue",
                    )
                )
                final = run.rounds[-1].student_prompt
                self.console.print(Panel(final.text, title=f"final student prompt v{final.version}", border_style="green"))
            self.console.print(f"[dim]saved to runs/{run.id}.json[/]")
        elif event.type == "error":
            self.console.print(f"[bold red]error[/] {event.message}")
        elif event.type == "log":
            self.console.print(f"[dim]{event.message}[/]")


def _report(events: Iterable[Event], reporter: Reporter) -> None:
    for event in events:
        reporter.handle(event)


async def _run(config: Config, task_path: Path, case_ids: list[str] | None, reporter: Reporter) -> None:
    task = load_task(task_path, case_ids)
    async for event in loop.run_loop(config, task):
        reporter.handle(event)


@app.command()
def run(
    task: Annotated[Optional[Path], typer.Option("--task", help="Task folder (default: from config)")] = None,
    case: CaseOpt = None,
    rounds: Annotated[Optional[int], typer.Option("--rounds", min=1, help="Max rounds")] = None,
    gap: Annotated[Optional[float], typer.Option("--gap", min=0.0, max=1.0, help="Stop when student >= teacher - gap")] = None,
    config: ConfigOpt = Path("config.yaml"),
    quiet: Annotated[bool, typer.Option("--quiet", help="Hide per-reply verdict lines")] = False,
) -> None:
    """Run the teacher/student/evaluator/coach loop until the gap closes."""
    cfg = load_config(config)
    if rounds is not None:
        cfg.loop.max_rounds = rounds
    if gap is not None:
        cfg.loop.gap = gap
    asyncio.run(_run(cfg, task or cfg.task, case, Reporter(console, verbose=not quiet)))


async def _test(cfg: Config, task_path: Path, case_id: str, agent_name: AgentName, prompt_file: Path | None, model: str | None) -> None:
    task = load_task(task_path, [case_id])
    the_case = task.cases[0]
    prompt = prompt_file.read_text().strip() if prompt_file else task.initial_prompt
    model_str = model or getattr(cfg.models, agent_name)
    agent = Agent(agent_name, model_str, prompt)
    record = await agent.run(the_case)
    verdict = await evaluator.grade(record, the_case, task=task, model=cfg.models.evaluator)
    console.print(Panel(prompt, title=f"prompt ({prompt_file or 'task v1'})", border_style="dim"))
    console.print(Panel(record.reply, title=f"{case_id} · {agent_name} ({model_str}) · {len(record.reply.split())} words", border_style="yellow"))
    console.print(Panel(verdict.reason, title=Text.assemble("score ", _fmt(verdict.score)), border_style=_score_style(verdict.score)))


@app.command()
def test(
    case: Annotated[str, typer.Option("--case", help="Case id")],
    agent: Annotated[str, typer.Option("--agent", help="teacher or student")] = "student",
    prompt: Annotated[Optional[Path], typer.Option("--prompt", help="Prompt file to use instead of task v1")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="LiteLLM model string override")] = None,
    task: Annotated[Optional[Path], typer.Option("--task", help="Task folder (default: from config)")] = None,
    config: ConfigOpt = Path("config.yaml"),
) -> None:
    """Run one agent on one case and grade it. No loop, no coach."""
    if agent not in ("teacher", "student"):
        raise typer.BadParameter("--agent must be 'teacher' or 'student'")
    cfg = load_config(config)
    asyncio.run(_test(cfg, task or cfg.task, case, agent, prompt, model))  # type: ignore[arg-type]


@app.command()
def replay(
    file: Annotated[Path, typer.Argument(help="runs/<ts>.json")],
    quiet: Annotated[bool, typer.Option("--quiet", help="Hide per-reply verdict lines")] = False,
) -> None:
    """Replay a saved run without spending tokens."""
    _report(loop.replay(loop.load_run(file)), Reporter(console, verbose=not quiet))


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port")] = 8000,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    config: ConfigOpt = Path("config.yaml"),
) -> None:
    """Serve the web UI."""
    import uvicorn

    from prompt_coach.server import create_app

    uvicorn.run(create_app(load_config(config)), host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
