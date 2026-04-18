from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_STATIC_DIR = _PACKAGE_DIR / "static"


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _error(status_code: int, message: str, **extra: Any) -> JSONResponse:
    body: Dict[str, Any] = {"error": message}
    body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_safe_qid(qid: str) -> bool:
    text = str(qid or "").strip()
    return bool(text and _SAFE_TOKEN.fullmatch(text))


def _resolve_recall_root_and_default_qid(raw_path: str) -> Tuple[Path, Optional[str]]:
    p = Path(str(raw_path or "").strip() or ".").resolve()
    if p.is_file() and p.suffix.lower() == ".json":
        return p.parent, p.stem
    if p.is_dir():
        # If this is a per-question folder: <recall_dir>/<qid>/Workspace/
        if (p / "Workspace").is_dir():
            return p.parent, p.name
        # Otherwise treat it as recall_dir containing <qid>.json
        return p, None
    raise ValueError(f"invalid path: {raw_path!r}")


def _discover_questions(recall_root: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not recall_root.is_dir():
        return items
    for path in sorted(recall_root.glob("*.json")):
        qid = path.stem
        if not _is_safe_qid(qid):
            continue
        has_ws = (recall_root / qid / "Workspace").is_dir()
        items.append(
            {
                "question_id": qid,
                "recall_json": str(path),
                "workspace_dir": str(recall_root / qid / "Workspace") if has_ws else "",
                "has_workspace_rounds": has_ws,
            }
        )
    return items


def _round_files_for_qid(recall_root: Path, qid: str) -> List[Path]:
    ws_dir = recall_root / qid / "Workspace"
    if not ws_dir.is_dir():
        return []
    files = [p for p in ws_dir.glob("round_*.json") if p.is_file()]
    files.sort(key=lambda p: p.name)
    return files


def _round_summary(round_payload: Dict[str, Any]) -> Dict[str, Any]:
    def _ws_count(obj: Any) -> int:
        if not isinstance(obj, dict):
            return 0
        ws = obj.get("evidences") if "evidences" in obj else obj.get("workspace", {}).get("evidences")
        return len(ws) if isinstance(ws, list) else 0

    judge = round_payload.get("judge_result") or round_payload.get("judge") or {}
    return {
        "round_id": round_payload.get("round_id"),
        "cur_query": round_payload.get("cur_query"),
        "status": round_payload.get("workspace_status") or round_payload.get("status"),
        "judge_status": judge.get("status") if isinstance(judge, dict) else None,
        "gap_type": judge.get("gap_type") if isinstance(judge, dict) else None,
        "evidence_count_before": _ws_count(round_payload.get("workspace_before")),
        "evidence_count_after_execute": _ws_count(round_payload.get("workspace_after_execute")),
        "evidence_count_after_rerank": _ws_count(round_payload.get("workspace_after_rerank")),
        "kept_evidence_count": len((round_payload.get("workspace_after_judge") or {}).get("kept_evidence_ids") or []),
    }


def create_app(
    *,
    recall_root: Path,
    default_qid: Optional[str] = None,
    static_dir: Optional[Path] = None,
) -> FastAPI:
    root = Path(recall_root).resolve()
    assets_dir = Path(static_dir).resolve() if static_dir is not None else _DEFAULT_STATIC_DIR

    app = FastAPI(title="M-Agent LongMemEval Recall Viewer", version="0.1")
    app.state.recall_root = root
    app.state.assets_dir = assets_dir
    app.state.default_qid = default_qid if _is_safe_qid(default_qid or "") else None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=600,
    )

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse(
            content={
                "status": "ok",
                "service": "longmemeval-recall-viewer",
                "recall_root": str(root),
                "default_question_id": app.state.default_qid,
            }
        )

    @app.get("/api/meta")
    def api_meta() -> JSONResponse:
        questions = _discover_questions(root)
        return JSONResponse(
            content={
                "recall_root": str(root),
                "question_count": len(questions),
                "default_question_id": app.state.default_qid or (questions[0]["question_id"] if questions else None),
            }
        )

    @app.get("/api/questions")
    def api_questions() -> JSONResponse:
        return JSONResponse(content={"count": len(_discover_questions(root)), "questions": _discover_questions(root)})

    @app.get("/api/questions/{question_id}/recall")
    def api_question_recall(question_id: str) -> JSONResponse:
        if not _is_safe_qid(question_id):
            return _error(400, "invalid question_id")
        path = root / f"{question_id}.json"
        if not path.is_file():
            return _error(404, "recall json not found", path=str(path))
        try:
            payload = _load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return _error(400, "failed to read recall json", detail=str(exc))
        return JSONResponse(content=payload)

    @app.get("/api/questions/{question_id}/rounds")
    def api_rounds(question_id: str) -> JSONResponse:
        if not _is_safe_qid(question_id):
            return _error(400, "invalid question_id")
        files = _round_files_for_qid(root, question_id)
        rows: List[Dict[str, Any]] = []
        for f in files:
            try:
                payload = _load_json(f)
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                rows.append({"file": f.name, "path": str(f), "summary": _round_summary(payload)})
            else:
                rows.append({"file": f.name, "path": str(f), "summary": {}})
        return JSONResponse(content={"question_id": question_id, "count": len(rows), "rounds": rows})

    @app.get("/api/questions/{question_id}/rounds/{round_file}")
    def api_round_detail(question_id: str, round_file: str) -> JSONResponse:
        if not _is_safe_qid(question_id):
            return _error(400, "invalid question_id")
        name = str(round_file or "").strip()
        if not name or "/" in name or "\\" in name or ".." in name:
            return _error(400, "invalid round filename")
        if not name.startswith("round_") or not name.lower().endswith(".json"):
            return _error(400, "invalid round filename")
        target = root / question_id / "Workspace" / name
        if not target.is_file():
            return _error(404, "round json not found", path=str(target))
        try:
            payload = _load_json(target)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return _error(400, "failed to read round json", detail=str(exc))
        return JSONResponse(content=payload)

    @app.get("/")
    def index() -> FileResponse:
        index_path = assets_dir / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=500, detail=f"missing UI bundle: {index_path}")
        return FileResponse(path=str(index_path), media_type="text/html")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize log/<test_id>/recall/ in the browser.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8096, help="Port to bind.")
    parser.add_argument(
        "--path",
        required=True,
        help=(
            "Path to a recall directory (log/<test_id>/recall), "
            "or a per-question recall json (recall/<qid>.json), "
            "or a per-question folder (recall/<qid>/)."
        ),
    )
    args = parser.parse_args()

    recall_root, default_qid = _resolve_recall_root_and_default_qid(args.path)
    app = create_app(recall_root=recall_root, default_qid=default_qid)

    import uvicorn

    uvicorn.run(app, host=str(args.host), port=max(1, int(args.port)))

