"""Local web control panel + live agent pilot for company-os-cli instances.

fastapi/uvicorn/httpx/etc. are mandatory dependencies of this package (its
only purpose is running this app), so there's no optional-extra story here —
contrast with the code this was split out of (`company-os-cli`'s old `web`
extra), where FastAPI was imported lazily to keep the base CLI dependency-light.

`create_app()` is pure (no socket bind, no browser launch) so it's cheap to
exercise in tests via `TestClient(create_app())`. `run_server()` is the only
place that actually binds a port or opens a browser, and lazy-imports
`uvicorn` purely to keep `create_app()` import-light for tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import webbrowser
from importlib import resources
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from company_os_cli import __version__
from company_os_cli.scaffold import (
    DEFAULT_EMBED_DIRNAME,
    MANIFEST_FILENAME,
    ScaffoldError,
    UpgradeError,
    scaffold,
    upgrade,
)

from . import board, pilot

STATIC_PACKAGE = "company_os_agent_ui"
STATIC_RESOURCE = "static/index.html"
OFFICE_STATIC_RESOURCE = "static/office.html"

logger = logging.getLogger(__name__)


class InitRequest(BaseModel):
    name: str
    product: str
    out: str = ""
    slug: str = ""
    force: bool = False
    llm_provider: str = "openrouter"
    model: str = "openrouter/free"
    langsmith_project: str = ""


class UpgradeRequest(BaseModel):
    out: str = ""
    name: Optional[str] = None
    product: Optional[str] = None
    dry_run: bool = False


def _resolve_out(out: str) -> Path:
    """Mirror company-os-cli's `--out` default so this UI and the CLI never diverge."""
    return Path(out).expanduser() if out else Path.cwd() / DEFAULT_EMBED_DIRNAME


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)


def create_app() -> FastAPI:
    app = FastAPI(title="Company OS Control Panel")

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"] if part != "body")
        return _error(f"{field}: {first['msg']}" if field else first["msg"], 422)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html_ref = resources.files(STATIC_PACKAGE) / STATIC_RESOURCE
        return html_ref.read_text(encoding="utf-8")

    @app.get("/office", response_class=HTMLResponse)
    def office() -> str:
        html_ref = resources.files(STATIC_PACKAGE) / OFFICE_STATIC_RESOURCE
        return html_ref.read_text(encoding="utf-8")

    @app.get("/api/defaults")
    def defaults() -> dict:
        return {
            "version": __version__,
            "cwd": str(Path.cwd()),
            "default_out": str(Path.cwd() / DEFAULT_EMBED_DIRNAME),
        }

    @app.post("/api/init")
    def api_init(payload: InitRequest) -> JSONResponse:
        try:
            result = scaffold(
                name=payload.name,
                product=payload.product,
                out=_resolve_out(payload.out),
                slug=payload.slug,
                force=payload.force,
                llm_provider=payload.llm_provider,
                model=payload.model,
                langsmith_project=payload.langsmith_project,
            )
        except ScaffoldError as exc:
            return _error(str(exc), 400)

        return JSONResponse(
            {
                "ok": True,
                "dest": str(result.dest),
                "project_root": str(result.project_root),
                "mapping": result.mapping,
                "leftover": result.leftover,
            }
        )

    @app.get("/api/board")
    def api_board(out: str = "") -> JSONResponse:
        instance_dir = _resolve_out(out)
        if not (instance_dir / MANIFEST_FILENAME).is_file():
            return _error(
                f"{instance_dir} is not a company-os instance "
                "(no manifest — run `company-os init` first).",
                400,
            )
        return JSONResponse({"ok": True, "instance": str(instance_dir), **board.build_board(instance_dir)})

    @app.get("/api/doc")
    def api_doc(out: str = "", path: str = "") -> JSONResponse:
        instance_dir = _resolve_out(out)
        if not (instance_dir / MANIFEST_FILENAME).is_file():
            return _error(
                f"{instance_dir} is not a company-os instance "
                "(no manifest — run `company-os init` first).",
                400,
            )
        try:
            return JSONResponse({"ok": True, **board.read_doc(instance_dir, path)})
        except board.DocReadError as exc:
            return _error(str(exc), exc.status_code)

    @app.post("/api/upgrade")
    def api_upgrade(payload: UpgradeRequest) -> JSONResponse:
        try:
            result = upgrade(
                out=_resolve_out(payload.out),
                name=payload.name,
                product=payload.product,
                dry_run=payload.dry_run,
            )
        except UpgradeError as exc:
            return _error(str(exc), 400)

        return JSONResponse(
            {
                "ok": True,
                "dest": str(result.dest),
                "project_root": str(result.project_root),
                "adopted": result.adopted,
                "dry_run": result.dry_run,
                "added": result.added,
                "updated": result.updated,
                "unchanged": result.unchanged,
                "conflicts": result.conflicts,
                "leftover": result.leftover,
            }
        )

    @app.websocket("/ws/run")
    async def ws_run(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            start = await websocket.receive_json()
        except WebSocketDisconnect:
            return

        out = str(start.get("out") or "")
        feature_request = str(start.get("feature_request") or "")
        instance_dir = _resolve_out(out)

        if not (instance_dir / MANIFEST_FILENAME).is_file():
            with contextlib.suppress(Exception):
                await websocket.send_json(
                    {
                        "type": "error",
                        "role": None,
                        "message": f"{instance_dir} is not a company-os instance "
                        "(no manifest — run `company-os init` first).",
                    }
                )
            await websocket.close(code=1008)
            return

        async def emit(event: dict) -> None:
            await websocket.send_json(event)

        try:
            await pilot.run_pilot(instance_dir, feature_request, emit=emit)
        except pilot.PilotError as exc:
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "role": None, "message": str(exc)})
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                await websocket.send_json(
                    {"type": "error", "role": None, "message": "Pilot run timed out."}
                )
        except WebSocketDisconnect:
            return
        except Exception:
            logger.exception("Unexpected error running pilot against %s", instance_dir)
            with contextlib.suppress(Exception):
                await websocket.send_json(
                    {
                        "type": "error",
                        "role": None,
                        "message": "Internal error running the pilot — see server logs.",
                    }
                )
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()

    return app


def run_server(*, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    if open_browser:
        url = f"http://{host}:{port}/"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(create_app(), host=host, port=port)
