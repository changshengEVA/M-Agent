from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .baseline_store import (
    build_dialogue_narrative,
    build_scene_summaries,
    count_entity_statement_json,
    discover_memory_workflow_roots,
    episode_situation_statistics_only,
    is_safe_dialogue_id,
    is_safe_workflow_id,
    iter_dialogue_json_files,
    iter_fact_json_files,
    load_facts_for_dialogue_from_files,
    load_json_if_exists,
    merge_file_facts_into_scene_layers,
    narrative_sources_used,
    resolve_memory_root,
    resolve_valid_memory_root,
    strip_embeddings,
    summarize_dialogue_file,
    memory_data_anchor,
    resolve_scene_file,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_STATIC_DIR = _PACKAGE_DIR / "static"


def _error(status_code: int, message: str, **extra: Any) -> JSONResponse:
    body: Dict[str, Any] = {"error": message}
    body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


def _load_dialogue_dict(root: Path, dialogue_id: str) -> Optional[Dict[str, Any]]:
    dialogues_dir = root / "dialogues"
    for path in iter_dialogue_json_files(dialogues_dir):
        try:
            payload = load_json_if_exists(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and str(payload.get("dialogue_id") or "") == dialogue_id:
            return payload
    return None


def _get_scene_cache_for_root(app: FastAPI, root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, str]]]]:
    if not hasattr(app.state, "scene_caches"):
        app.state.scene_caches = {}
    caches: Dict[str, Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, str]]]]] = app.state.scene_caches
    key = str(root.resolve())
    if key not in caches:
        scene_dir = root / "scene"
        caches[key] = build_scene_summaries(scene_dir)
    return caches[key]


def create_app(
    *,
    workflow_id: str = "baseline",
    memory_root: Optional[Path] = None,
    static_dir: Optional[Path] = None,
) -> FastAPI:
    wf = str(workflow_id or "baseline").strip() or "baseline"
    if not is_safe_workflow_id(wf):
        raise ValueError(f"unsafe workflow_id: {workflow_id!r}")
    if memory_root is not None:
        root = Path(memory_root).resolve()
    else:
        root = resolve_memory_root(workflow_id=wf)

    assets_dir = Path(static_dir).resolve() if static_dir is not None else _DEFAULT_STATIC_DIR

    app = FastAPI(title="M-Agent Memory Baseline Explorer", version="1.1")
    app.state.workflow_id = wf
    app.state.memory_root = root
    app.state.scene_caches = {}
    app.state.assets_dir = assets_dir

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=600,
    )

    anchor = memory_data_anchor()

    def get_effective_root_from_request(request: Request) -> Path:
        q = request.query_params.get("memory_root")
        h = request.headers.get("x-memory-root") or request.headers.get("X-Memory-Root")
        raw = (q or h or "").strip()
        if raw:
            resolved = resolve_valid_memory_root(raw, anchor=anchor)
            if resolved is None:
                raise HTTPException(status_code=400, detail="invalid memory_root")
            return resolved
        return app.state.memory_root

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/healthz")
    def healthz(root: Path = Depends(get_effective_root_from_request)) -> JSONResponse:
        return JSONResponse(
            content={
                "status": "ok",
                "service": "memory-baseline-explorer",
                "workflow_id": wf,
                "memory_root": str(root),
            }
        )

    @app.get("/api/registry")
    def api_registry(max_depth: int = 10, max_roots: int = 500) -> JSONResponse:
        rows = discover_memory_workflow_roots(
            anchor=anchor,
            max_depth=max(1, min(max_depth, 20)),
            max_roots=max(1, min(max_roots, 2000)),
        )
        return JSONResponse(content={"memory_anchor": str(anchor), "count": len(rows), "workflows": rows})

    @app.get("/api/global-stats")
    def api_global_stats(
        max_depth: int = 10,
        max_workflows: int = 80,
        per_root_cap: int = 5000,
    ) -> JSONResponse:
        rows = discover_memory_workflow_roots(anchor=anchor, max_depth=max_depth, max_roots=500)
        dialogue_total = 0
        summaries: List[Dict[str, Any]] = []
        for item in rows[: max(1, min(max_workflows, 200))]:
            r = Path(item["memory_root"])
            dlg_n = len(iter_dialogue_json_files(r / "dialogues"))
            if dlg_n > per_root_cap:
                dlg_n = per_root_cap
            dialogue_total += dlg_n
            summaries.append(
                {
                    "relative_path": item.get("relative_path"),
                    "memory_root": item.get("memory_root"),
                    "dialogue_file_count": dlg_n,
                }
            )
        return JSONResponse(
            content={
                "memory_anchor": str(anchor),
                "workflow_root_count": len(rows),
                "dialogue_file_total_scanned": dialogue_total,
                "workflows": summaries,
            }
        )

    @app.get("/api/meta")
    def api_meta(root: Path = Depends(get_effective_root_from_request)) -> JSONResponse:
        return JSONResponse(
            content={
                "workflow_id": wf,
                "memory_root": str(root),
                "paths": {
                    "dialogues": str(root / "dialogues"),
                    "dialogues_exists": (root / "dialogues").is_dir(),
                    "episodes": str(root / "episodes"),
                    "episodes_exists": (root / "episodes").is_dir(),
                    "scene": str(root / "scene"),
                    "scene_exists": (root / "scene").is_dir(),
                    "facts": str(root / "facts"),
                    "facts_exists": (root / "facts").is_dir(),
                    "entity_statement": str(root / "entity_statement"),
                    "entity_statement_exists": (root / "entity_statement").is_dir(),
                    "facts_situation": str(root / "facts_situation.json"),
                    "facts_situation_exists": (root / "facts_situation.json").is_file(),
                },
            }
        )

    @app.get("/api/overview")
    def api_overview(root: Path = Depends(get_effective_root_from_request)) -> JSONResponse:
        dialogues_dir = root / "dialogues"
        scene_dir = root / "scene"
        by_dialogue_root = root / "episodes" / "by_dialogue"
        dialogue_files = iter_dialogue_json_files(dialogues_dir)
        by_dialogue_count = 0
        if by_dialogue_root.is_dir():
            by_dialogue_count = sum(1 for p in by_dialogue_root.iterdir() if p.is_dir())
        scene_count = 0
        if scene_dir.is_dir():
            scene_count = sum(1 for p in scene_dir.glob("*.json") if p.is_file())
        situation_path = root / "episodes" / "episode_situation.json"
        situation_stats = episode_situation_statistics_only(situation_path)
        summaries, _ = _get_scene_cache_for_root(app, root)
        facts_in_scenes_total = sum(int(s.get("fact_count") or 0) for s in summaries)
        fact_files = iter_fact_json_files(root / "facts")
        entity_stmt_n = count_entity_statement_json(root / "entity_statement")
        return JSONResponse(
            content={
                "workflow_id": wf,
                "memory_root": str(root),
                "dialogue_file_count": len(dialogue_files),
                "scene_file_count": scene_count,
                "by_dialogue_dir_count": by_dialogue_count,
                "episode_situation_path": str(situation_path),
                "episode_situation_statistics": situation_stats,
                "facts_in_scenes_total": facts_in_scenes_total,
                "scenes_with_fact_counts": summaries,
                "fact_json_file_count": len(fact_files),
                "entity_statement_json_file_count": entity_stmt_n,
                "facts_situation_file_exists": (root / "facts_situation.json").is_file(),
            }
        )

    @app.get("/api/dialogues")
    def api_dialogues(root: Path = Depends(get_effective_root_from_request)) -> JSONResponse:
        dialogues_dir = root / "dialogues"
        items: List[Dict[str, Any]] = []
        for path in iter_dialogue_json_files(dialogues_dir):
            try:
                items.append(summarize_dialogue_file(path, rel_root=root))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        items.sort(key=lambda row: str(row.get("dialogue_id") or ""))
        return JSONResponse(content={"count": len(items), "dialogues": items})

    @app.get("/api/dialogues/{dialogue_id}")
    def api_dialogue_detail(dialogue_id: str, root: Path = Depends(get_effective_root_from_request)) -> JSONResponse:
        if not is_safe_dialogue_id(dialogue_id):
            return _error(400, "invalid dialogue_id")
        payload = _load_dialogue_dict(root, dialogue_id)
        if payload is None:
            return _error(404, "dialogue not found", dialogue_id=dialogue_id)
        return JSONResponse(content=payload)

    @app.get("/api/dialogues/{dialogue_id}/narrative")
    def api_dialogue_narrative(dialogue_id: str, root: Path = Depends(get_effective_root_from_request)) -> JSONResponse:
        if not is_safe_dialogue_id(dialogue_id):
            return _error(400, "invalid dialogue_id")
        dialogue = _load_dialogue_dict(root, dialogue_id)
        if dialogue is None:
            return _error(404, "dialogue not found", dialogue_id=dialogue_id)
        episodes_path = root / "episodes" / "by_dialogue" / dialogue_id / "episodes_v1.json"
        episodes_doc = load_json_if_exists(episodes_path)
        if not isinstance(episodes_doc, dict):
            episodes_doc = None
        _, by_dialogue = _get_scene_cache_for_root(app, root)
        hits = list(by_dialogue.get(dialogue_id, []))
        scene_dir = root / "scene"
        layers: List[Dict[str, Any]] = []
        for hit in hits:
            raw_name = str(hit.get("file") or "")
            stem = raw_name[:-5] if raw_name.lower().endswith(".json") else raw_name
            target = resolve_scene_file(scene_dir, stem)
            if target is None:
                continue
            try:
                data = load_json_if_exists(target)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            stripped = strip_embeddings(dict(data))
            stripped["_file"] = target.name
            layers.append(stripped)
        had_scene_layers = bool(layers)
        facts_dir = root / "facts"
        file_facts: List[Dict[str, Any]] = []
        if facts_dir.is_dir():
            file_facts = load_facts_for_dialogue_from_files(facts_dir, dialogue_id)
        merged_layers = merge_file_facts_into_scene_layers(layers, file_facts)
        narrative = build_dialogue_narrative(dialogue, episodes_doc, merged_layers)
        narrative["episodes_source_path"] = str(episodes_path)
        narrative["scene_hits"] = hits
        narrative["narrative_source"] = narrative_sources_used(
            had_scene_layers=had_scene_layers,
            file_facts=file_facts,
        )
        return JSONResponse(content=narrative)

    @app.get("/api/dialogues/{dialogue_id}/episodes")
    def api_dialogue_episodes(dialogue_id: str, root: Path = Depends(get_effective_root_from_request)) -> JSONResponse:
        if not is_safe_dialogue_id(dialogue_id):
            return _error(400, "invalid dialogue_id")
        base = root / "episodes" / "by_dialogue" / dialogue_id
        episodes_path = base / "episodes_v1.json"
        eligibility_path = base / "eligibility_v1.json"
        episodes_payload: Any = None
        eligibility_payload: Any = None
        episodes_error: Optional[str] = None
        eligibility_error: Optional[str] = None
        if episodes_path.is_file():
            try:
                episodes_payload = load_json_if_exists(episodes_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                episodes_error = str(exc)
        else:
            episodes_error = "file_missing"
        if eligibility_path.is_file():
            try:
                eligibility_payload = load_json_if_exists(eligibility_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                eligibility_error = str(exc)
        else:
            eligibility_error = "file_missing"
        return JSONResponse(
            content={
                "dialogue_id": dialogue_id,
                "episodes_path": str(episodes_path),
                "eligibility_path": str(eligibility_path),
                "episodes": episodes_payload,
                "eligibility": eligibility_payload,
                "episodes_error": episodes_error,
                "eligibility_error": eligibility_error,
            }
        )

    @app.get("/api/scenes")
    def api_scenes(root: Path = Depends(get_effective_root_from_request)) -> JSONResponse:
        summaries, by_dialogue = _get_scene_cache_for_root(app, root)
        return JSONResponse(
            content={
                "count": len(summaries),
                "scenes": summaries,
                "dialogue_to_scenes": by_dialogue,
            }
        )

    @app.get("/api/scenes/{stem}")
    def api_scene_detail(
        stem: str,
        omit_embeddings: bool = True,
        root: Path = Depends(get_effective_root_from_request),
    ) -> JSONResponse:
        scene_dir = root / "scene"
        target = resolve_scene_file(scene_dir, stem)
        if target is None:
            return _error(404, "scene not found", stem=stem)
        try:
            payload = load_json_if_exists(target)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return _error(400, "failed to read scene json", detail=str(exc))
        if omit_embeddings:
            payload = strip_embeddings(payload)
        return JSONResponse(
            content={
                "file": target.name,
                "path": str(target),
                "omit_embeddings": bool(omit_embeddings),
                "data": payload,
            }
        )

    @app.get("/api/dialogues/{dialogue_id}/related-scenes")
    def api_related_scenes(dialogue_id: str, root: Path = Depends(get_effective_root_from_request)) -> JSONResponse:
        if not is_safe_dialogue_id(dialogue_id):
            return _error(400, "invalid dialogue_id")
        _, by_dialogue = _get_scene_cache_for_root(app, root)
        hits = list(by_dialogue.get(dialogue_id, []))
        return JSONResponse(content={"dialogue_id": dialogue_id, "scenes": hits})

    @app.get("/")
    def index() -> FileResponse:
        index_path = assets_dir / "index.html"
        if not index_path.is_file():
            raise RuntimeError(f"missing UI bundle: {index_path}")
        return FileResponse(path=str(index_path), media_type="text/html")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Browse data/memory/<workflow_id> in the browser.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8093, help="Port to bind.")
    parser.add_argument(
        "--workflow",
        default="baseline",
        help="Workflow id under data/memory (default: baseline).",
    )
    parser.add_argument(
        "--memory-root",
        default="",
        help="Override memory root directory (advanced).",
    )
    args = parser.parse_args()
    wf = str(args.workflow or "baseline").strip() or "baseline"
    if args.memory_root and str(args.memory_root).strip():
        app = create_app(workflow_id=wf, memory_root=Path(str(args.memory_root).strip()))
    else:
        if not is_safe_workflow_id(wf):
            raise SystemExit(f"unsafe workflow_id: {wf!r}")
        app = create_app(workflow_id=wf)

    import uvicorn

    uvicorn.run(app, host=str(args.host), port=max(1, int(args.port)))
