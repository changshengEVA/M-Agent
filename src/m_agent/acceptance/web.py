"""Local-only FastAPI dashboard for semantic acceptance runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse

from .catalog import catalog_payload
from .manager import RunManager
from .runner import AcceptanceRunner, RunBusyError


_UI_ROOT = Path(__file__).resolve().parent / "ui"
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


def create_app(
    *,
    runner: Optional[AcceptanceRunner] = None,
    manager: Optional[RunManager] = None,
) -> FastAPI:
    """Create an isolated app; it is never mounted into the production Chat API."""

    active_runner = runner or AcceptanceRunner()
    active_manager = manager or RunManager(active_runner)
    app = FastAPI(
        title="M-Agent Semantic Acceptance",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.acceptance_runner = active_runner
    app.state.acceptance_manager = active_manager

    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Any):
        response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_UI_ROOT / "index.html", media_type="text/html")

    @app.get("/app.js", include_in_schema=False)
    def app_js() -> FileResponse:
        return FileResponse(
            _UI_ROOT / "app.js",
            media_type="application/javascript",
        )

    @app.get("/style.css", include_in_schema=False)
    def style_css() -> FileResponse:
        return FileResponse(_UI_ROOT / "style.css", media_type="text/css")

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "service": "m-agent-acceptance",
            "local_only": True,
        }

    @app.get("/api/catalog")
    def catalog() -> Dict[str, Any]:
        return catalog_payload()

    @app.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
    async def start_run(request: Request) -> Dict[str, Any]:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="request body must be JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="request body must be an object")
        extra = set(body) - {"invariant_ids", "profile", "timeout_seconds"}
        if extra:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported field(s): {', '.join(sorted(extra))}",
            )
        invariant_ids = body.get("invariant_ids")
        if invariant_ids is not None and not isinstance(invariant_ids, list):
            raise HTTPException(status_code=400, detail="invariant_ids must be a list")
        profile = str(body.get("profile", "gate") or "gate")
        try:
            timeout_seconds = float(body.get("timeout_seconds", 300.0))
            return active_manager.start(
                invariant_ids=invariant_ids,
                profile=profile,
                timeout_seconds=timeout_seconds,
            )
        except RunBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> Dict[str, Any]:
        try:
            payload = active_manager.get(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        if payload is None:
            raise HTTPException(status_code=404, detail="run not found")
        return payload

    @app.post("/api/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    def cancel_run(run_id: str) -> Dict[str, Any]:
        try:
            cancelled = active_manager.cancel(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        if not cancelled:
            raise HTTPException(status_code=409, detail="run is not active")
        payload = active_manager.get(run_id)
        return payload or {"run_id": run_id, "status": "cancelling"}

    @app.get("/api/runs/{run_id}/artifacts/{artifact_name}")
    def get_artifact(run_id: str, artifact_name: str) -> FileResponse:
        try:
            path = active_runner.store.artifact_path(run_id, artifact_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="artifact not found")
        media_type = (
            "application/json"
            if path.suffix == ".json"
            else "application/xml"
            if path.suffix == ".xml"
            else "text/plain"
        )
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.exception_handler(RunBusyError)
    async def _busy_handler(request: Request, exc: RunBusyError) -> JSONResponse:
        del request
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    return app
