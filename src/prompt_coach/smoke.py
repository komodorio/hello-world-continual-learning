"""Smoke utility: run the teacher and the student on every case and print the raw replies.

No grading, no coach. Useful for eyeballing the gap between the two models before you trust
the evaluator, or after you edit a case.

    uv run python -m prompt_coach.smoke [--case ID ...] [--config config.yaml]
"""

from __future__ import annotations

import argparse
import asyncio

from rich.console import Console
from rich.panel import Panel

from prompt_coach.agent import Agent
from prompt_coach.config import load_config
from prompt_coach.task import load_task
from prompt_coach.types import Record


async def _main(config_path: str, case_ids: list[str]) -> None:
    config = load_config(config_path)
    task = load_task(config.task, case_ids or None)
    prompt = task.initial_prompt
    agents = [
        Agent("teacher", config.models.teacher, prompt),
        Agent("student", config.models.student, prompt),
    ]
    console = Console()
    console.print(Panel(prompt, title="prompt v1", border_style="dim"))

    jobs = [agent.run(case) for case in task.cases for agent in agents]
    records: list[Record] = list(await asyncio.gather(*jobs))
    for record in records:
        words = len(record.reply.split())
        console.print(
            Panel(
                record.reply,
                title=f"[bold]{record.case_id}[/] · {record.agent} ({record.model}) · {words} words · {record.latency_s}s",
                border_style="green" if record.agent == "teacher" else "yellow",
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", action="append", default=[], help="case id; repeatable")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    asyncio.run(_main(args.config, args.case))


if __name__ == "__main__":
    main()
