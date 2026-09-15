"""Typer CLI for company-os-agent-ui (see pyproject.toml `company-os-agent-ui` script).

This package's only purpose is running the web control panel + live agent
pilot, so there's no subcommand to type — `company-os-agent-ui [OPTIONS]`
starts the server directly. fastapi/uvicorn are mandatory dependencies of
this package (not an optional extra), so `run_server` is imported at module
scope with no ImportError fallback needed.
"""

from __future__ import annotations

from typing import Optional

import typer

from . import __version__
from .app import run_server

app = typer.Typer(
    name="company-os-agent-ui",
    help="Local web control panel + live PM/Architect/Backend agent pilot for company-os-cli instances.",
    add_completion=False,
    invoke_without_command=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"company-os-agent-ui {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind address. Local-only by default; only change this if you understand "
        "it exposes filesystem-writing endpoints on your network.",
    ),
    port: int = typer.Option(8765, "--port", help="Port to listen on."),
    open_browser: bool = typer.Option(
        True,
        "--open-browser/--no-browser",
        help="Open the control panel in your default browser on startup.",
    ),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the company-os-agent-ui version and exit.",
    ),
) -> None:
    """Start the control panel (`/`) and live agent pilot (`/office`)."""
    typer.echo(f"Starting Company OS control panel at http://{host}:{port}/ (Ctrl+C to stop)")
    typer.echo(f"  live agent pilot: http://{host}:{port}/office")
    try:
        run_server(host=host, port=port, open_browser=open_browser)
    except OSError as exc:
        typer.secho(f"Could not start server: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
