#!/usr/bin/env python3

"""Run mini-SWE-agent in your local environment. This is the default executable `mini`."""
# Read this first: https://mini-swe-agent.com/latest/usage/mini/  (usage)

import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from minisweagent import global_config_dir
from minisweagent.agents import get_agent
from minisweagent.agents.session_store import SessionStore
from minisweagent.agents.utils.prompt_user import _multiline_prompt
from minisweagent.config import builtin_config_dir, get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.run.utilities.config import configure_if_first_time
from minisweagent.utils.serialize import UNSET, recursive_merge

DEFAULT_CONFIG_FILE = Path(os.getenv("MSWEA_MINI_CONFIG_PATH", builtin_config_dir / "mini.yaml"))
DEFAULT_OUTPUT_FILE = global_config_dir / "last_mini_run.traj.json"


_HELP_TEXT = """Run mini-SWE-agent in your local environment.

[not dim]
More information about the usage: [bold green]https://mini-swe-agent.com/latest/usage/mini/[/bold green]
[/not dim]
"""

_CONFIG_SPEC_HELP_TEXT = """Path to config files, filenames, or key-value pairs.

[bold red]IMPORTANT:[/bold red] [red]If you set this option, the default config file will not be used.[/red]
So you need to explicitly set it e.g., with [bold green]-c mini.yaml <other options>[/bold green]

Multiple configs will be recursively merged.

Examples:

[bold red]-c model.model_kwargs.temperature=0[/bold red] [red]You forgot to add the default config file! See above.[/red]

[bold green]-c mini.yaml -c model.model_kwargs.temperature=0.5[/bold green]

[bold green]-c swebench.yaml agent.mode=yolo[/bold green]
"""

console = Console(highlight=False)
app = typer.Typer(rich_markup_mode="rich")


# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    model_name: str | None = typer.Option(None, "-m", "--model", help="Model to use",),
    model_class: str | None = typer.Option(None, "--model-class", help="Model class to use (e.g., 'litellm' or 'minisweagent.models.litellm_model.LitellmModel')", rich_help_panel="Advanced"),
    agent_class: str | None = typer.Option(None, "--agent-class", help="Agent class to use (e.g., 'interactive' or 'minisweagent.agents.interactive.InteractiveAgent')", rich_help_panel="Advanced"),
    environment_class: str | None = typer.Option(None, "--environment-class", help="Environment class to use (e.g., 'local' or 'minisweagent.environments.local.LocalEnvironment')", rich_help_panel="Advanced"),
    task: str | None = typer.Option(None, "-t", "--task", help="Task/problem statement", show_default=False),
    yolo: bool = typer.Option(False, "-y", "--yolo", help="Run without confirmation"),
    cost_limit: float | None = typer.Option(None, "-l", "--cost-limit", help="Cost limit. Set to 0 to disable."),
    config_spec: list[str] = typer.Option([str(DEFAULT_CONFIG_FILE)], "-c", "--config", help=_CONFIG_SPEC_HELP_TEXT),
    output: Path | None = typer.Option(None, "-o", "--output", help="Output trajectory file (default: last_mini_run.traj.json; for --resume: <original>.resumed.json)"),
    exit_immediately: bool = typer.Option(False, "--exit-immediately", help="Exit immediately when the agent wants to finish instead of prompting.", rich_help_panel="Advanced"),
    session_db: Path | None = typer.Option(None, "--session-db", help="魔改 4: SQLite file for session persistence — enables recording, --resume and --list-sessions.", rich_help_panel="Advanced"),
    resume_session: str | None = typer.Option(None, "--resume", help="魔改 4: resume a persisted session id (requires --session-db or agent.session_db_path in the config).", rich_help_panel="Advanced"),
    list_sessions: bool | None = typer.Option(None, "--list-sessions", help="魔改 4: list persisted sessions and exit.", rich_help_panel="Advanced"),
) -> Any:
    # fmt: on
    configure_if_first_time()

    # 魔改 4: when main() is called directly (tests, tools), unpassed typer options
    # arrive as OptionInfo objects instead of their defaults — normalize them.
    if not isinstance(session_db, Path):
        session_db = None
    if not isinstance(resume_session, str):
        resume_session = None
    if not isinstance(list_sessions, bool):
        list_sessions = False
    if output is not None and not isinstance(output, Path):
        output = None

    # Build the config from the command line arguments
    console.print(f"Building agent config from specs: [bold green]{config_spec}[/bold green]")
    configs = [get_config_from_spec(spec) for spec in config_spec]
    configs.append({
        "run": {
            "task": task or UNSET,
        },
        "agent": {
            "agent_class": agent_class or UNSET,
            "mode": "yolo" if yolo else UNSET,
            "cost_limit": cost_limit if cost_limit is not None else UNSET,
            "confirm_exit": False if exit_immediately else UNSET,
            "output_path": output or UNSET,
        },
        "model": {
            "model_class": model_class or UNSET,
            "model_name": model_name or UNSET,
        },
        "environment": {
            "environment_class": environment_class or UNSET,
        },
    })
    config = recursive_merge(*configs)

    if session_db is not None:
        config.setdefault("agent", {})["session_db_path"] = str(session_db)
    db_path = session_db or config.get("agent", {}).get("session_db_path", "")

    if list_sessions:
        if not db_path:
            console.print("[bold red]No session DB configured.[/bold red] Pass --session-db or set agent.session_db_path.")
            raise typer.Exit(1)
        import time as _time

        table = rich_table()
        for s in SessionStore.list_sessions(db_path):
            table.add_row(
                s["session_id"],
                s["status"],
                _time.strftime("%m-%d %H:%M", _time.localtime(s["updated_at"])),
                f"${s['cost']:.2f}",
                str(s["n_calls"]),
                (s["task"] or "")[:60],
            )
        console.print(table)
        return None

    resume_state = None
    if resume_session:
        if not db_path:
            console.print("[bold red]--resume requires a session DB.[/bold red] Pass --session-db or set agent.session_db_path.")
            raise typer.Exit(1)
        try:
            resume_state = SessionStore.load_session(db_path, resume_session)
        except (FileNotFoundError, ValueError) as e:
            console.print(f"[bold red]{e}[/bold red]")
            raise typer.Exit(1) from e
        if resume_state["status"] == "finished":
            console.print(
                f"[bold yellow]Session {resume_session} is already finished"
                f" (exit_status={resume_state['exit_status']!r})[/bold yellow] — nothing to resume."
            )
            return None
        # 魔改 4: overlay the persisted agent config (runtime-mutated limits etc.) over
        # the yaml config. The old output_path / session_db_path never carry over.
        snapshot = dict(resume_state.get("config_snapshot") or {})
        snapshot.pop("output_path", None)
        snapshot.pop("session_db_path", None)
        config["agent"] = recursive_merge(config.get("agent", {}), snapshot)

    if (run_task := config.get("run", {}).get("task", UNSET)) is UNSET:
        if resume_state is not None:
            run_task = resume_state.get("task", "")
            console.print(f"Resuming task: [bold green]{run_task}[/bold green]")
        else:
            console.print("[bold yellow]What do you want to do?")
            run_task = _multiline_prompt()
            console.print("[bold green]Got that, thanks![/bold green]")

    if output is None:
        if resume_state is not None:
            original = (resume_state.get("config_snapshot") or {}).get("output_path") or "resumed_session.traj.json"
            output = Path(f"{original}.resumed.json")
        else:
            output = DEFAULT_OUTPUT_FILE
    config.setdefault("agent", {})["output_path"] = str(output)

    model = get_model(config=config.get("model", {}))
    env = get_environment(config.get("environment", {}), default_type="local")
    agent = get_agent(model, env, config.get("agent", {}), default_type="interactive")
    if resume_state is not None:
        agent.resume(resume_session)
    else:
        agent.run(run_task)
    if (output_path := config.get("agent", {}).get("output_path")):
        console.print(f"Saved trajectory to [bold green]'{output_path}'[/bold green]")
    if getattr(agent, "session_id", ""):
        hint = f"mini --resume {agent.session_id} --session-db {db_path or '<db>'}" if db_path else ""
        if hint:
            console.print(f"Session: [bold green]{agent.session_id}[/bold green] (resume: [dim]{hint}[/dim])")
    return agent


def rich_table():
    from rich.table import Table

    table = Table(title="Persisted sessions")
    table.add_column("session_id")
    table.add_column("status")
    table.add_column("updated")
    table.add_column("cost")
    table.add_column("steps")
    table.add_column("task")
    return table


if __name__ == "__main__":
    app()
