"""Command line interface."""

import functools
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from nested_runner import auth, config
from nested_runner.errors import NestedRunnerError
from nested_runner.github import GitHub
from nested_runner.loop import Scheduler

app = typer.Typer(
    help="Ephemeral GitHub Actions runners running inside GitHub Actions.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


def friendly[**P, R](command: Callable[P, R]) -> Callable[P, R]:
    """Print NestedRunnerError as a panel instead of a traceback."""

    @functools.wraps(command)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return command(*args, **kwargs)
        except NestedRunnerError as error:
            body = error.message
            if error.hint:
                body += f"\n\n[dim]{error.hint}[/dim]"
            console.print(Panel(body, title="[red]ошибка", border_style="red"))
            raise typer.Exit(1) from None
        except KeyboardInterrupt:
            console.print("\n[dim]остановлено[/dim]")
            raise typer.Exit(0) from None

    return wrapper


@app.callback()
def main(
    ctx: typer.Context,
    config_dir: Annotated[
        Path | None,
        typer.Option("--config-dir", help="Override the config directory."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show debug logging."),
    ] = False,
) -> None:
    """Set up logging and remember where the configuration lives."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
    )
    ctx.obj = config_dir or config.config_dir()


def _setup(ctx: typer.Context) -> tuple[config.Config, GitHub]:
    """Load config and token, build an API client."""
    cfg = config.load(ctx.obj)
    token = auth.load(ctx.obj)
    return cfg, GitHub(token)


@app.command()
@friendly
def login(ctx: typer.Context) -> None:
    """Save a personal access token and verify repository access."""
    cfg = config.load(ctx.obj)

    token = typer.prompt("GitHub PAT", hide_input=True).strip()
    if not token:
        raise NestedRunnerError("пустой токен")

    with GitHub(token) as github:
        user = github.whoami()
        for repo in cfg.repos:
            github.check_repo(repo.slug)

    auth.save(ctx.obj, token)
    console.print(f"[green]готово[/green], вошли как [bold]{user}[/bold]")
    console.print(f"[dim]{auth.token_path(ctx.obj)}[/dim]")


@app.command()
@friendly
def status(ctx: typer.Context) -> None:
    """Show what the controller sees right now, without dispatching."""
    cfg, github = _setup(ctx)

    with github:
        scheduler = Scheduler(github)
        rows = [(repo, scheduler.plan(repo)) for repo in cfg.repos]

    table = Table(box=None, pad_edge=False)
    table.add_column("repo")
    for column in ("online", "idle", "inflight", "need"):
        table.add_column(column, justify="right")

    for repo, plan in rows:
        table.add_row(
            repo.slug,
            str(plan.online),
            str(plan.idle),
            str(plan.inflight),
            f"[yellow]{plan.need}[/yellow]" if plan.need else "0",
        )
    console.print(table)


@app.command()
@friendly
def run(
    ctx: typer.Context,
    once: Annotated[
        bool,
        typer.Option("--once", help="Run a single tick and exit."),
    ] = False,
) -> None:
    """Keep the warm pool topped up."""
    cfg, github = _setup(ctx)

    with github:
        scheduler = Scheduler(github)
        if once:
            for repo in cfg.repos:
                scheduler.tick(repo)
        else:
            scheduler.serve(cfg)
