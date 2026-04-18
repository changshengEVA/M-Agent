from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from m_agent.paths import DATA_DIR, memory_workflow_dir

_WORKFLOW_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
_SCENE_ID_RE = re.compile(r'"scene_id"\s*:\s*"([^"]*)"')
_DIALOGUE_ID_RE = re.compile(r'"dialogue_id"\s*:\s*"([^"]*)"')
_ATOMIC_FACT_KEY_RE = re.compile(r'"Atomic fact"\s*:')


def is_safe_workflow_id(workflow_id: str) -> bool:
    s = str(workflow_id or "").strip()
    return bool(s) and ".." not in s and "/" not in s and "\\" not in s and bool(_WORKFLOW_ID_RE.match(s))


def is_safe_dialogue_id(dialogue_id: str) -> bool:
    s = str(dialogue_id or "").strip()
    if not s or ".." in s or "/" in s or "\\" in s:
        return False
    return True


def is_safe_scene_stem(stem: str) -> bool:
    s = str(stem or "").strip()
    if not s or ".." in s or "/" in s or "\\" in s:
        return False
    return True


def resolve_memory_root(*, workflow_id: str) -> Path:
    return memory_workflow_dir(workflow_id).resolve()


def memory_data_anchor() -> Path:
    """Canonical root for all on-disk memory workflows: ``data/memory``."""
    return (DATA_DIR / "memory").resolve()


def resolve_valid_memory_root(
    raw: str | Path | None,
    *,
    anchor: Optional[Path] = None,
) -> Optional[Path]:
    """
    Resolve a directory under ``data/memory`` from an absolute path or a path
    relative to ``anchor`` (e.g. ``locomo/conv-26``). Returns None if invalid.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    base = (anchor or memory_data_anchor()).resolve()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    else:
        path = path.resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    if not path.is_dir():
        return None
    return path


def _is_memory_workflow_candidate(dir_path: Path) -> bool:
    """True for a directory that looks like a memory *workflow root* (not ``.../dialogues`` alone)."""
    if not dir_path.is_dir():
        return False
    episodes = dir_path / "episodes"
    if not episodes.is_dir():
        return False
    by_dlg = episodes / "by_dialogue"
    if by_dlg.is_dir():
        return True
    dialogues = dir_path / "dialogues"
    if dialogues.is_dir():
        for p in dialogues.rglob("*.json"):
            if p.is_file():
                return True
    return False


def discover_memory_workflow_roots(
    *,
    anchor: Optional[Path] = None,
    max_depth: int = 10,
    max_roots: int = 500,
) -> List[Dict[str, Any]]:
    """
    Find directories under ``data/memory`` that look like a workflow root
    (has ``dialogues`` with JSON and/or ``episodes/by_dialogue``).
    """
    base = (anchor or memory_data_anchor()).resolve()
    if not base.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    base_parts = len(base.parts)

    def walk(current: Path, depth: int) -> None:
        if len(out) >= max_roots:
            return
        if depth > max_depth:
            return
        if not current.is_dir():
            return
        try:
            if current.resolve() != base and _is_memory_workflow_candidate(current):
                rel = current.resolve().relative_to(base)
                out.append(
                    {
                        "relative_path": str(rel).replace("\\", "/"),
                        "memory_root": str(current.resolve()),
                    }
                )
                if len(out) >= max_roots:
                    return
        except ValueError:
            return
        try:
            for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith("."):
                    continue
                if len(out) >= max_roots:
                    return
                walk(child, depth + 1)
        except OSError:
            return

    walk(base, 0)
    # Prefer deeper roots: drop a path if a more specific child is also listed
    roots = [str(item["memory_root"]) for item in out]
    roots_set = set(roots)
    filtered: List[Dict[str, Any]] = []
    for item in out:
        p = Path(item["memory_root"])
        skip = False
        for other in roots_set:
            if other == str(p):
                continue
            try:
                Path(other).resolve().relative_to(p)
                skip = True
                break
            except ValueError:
                continue
        if not skip:
            filtered.append(item)
    filtered.sort(key=lambda row: str(row.get("relative_path") or ""))
    return filtered


def iter_fact_json_files_deep(facts_dir: Path, *, max_files: int = 8000) -> List[Path]:
    if not facts_dir.is_dir():
        return []
    paths: List[Path] = []
    for p in sorted(facts_dir.rglob("*.json")):
        if p.is_file():
            paths.append(p)
            if len(paths) >= max_files:
                break
    return paths


def _normalize_fact_dict(obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(obj, dict) and (obj.get("Atomic fact") is not None or obj.get("evidence") is not None):
        return obj
    return None


def load_facts_for_dialogue_from_files(facts_dir: Path, dialogue_id: str) -> List[Dict[str, Any]]:
    """Load atomic facts from ``facts/**/*.json`` that belong to ``dialogue_id``."""
    out: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str, str]] = set()
    for path in iter_fact_json_files_deep(facts_dir):
        try:
            data = load_json_if_exists(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        candidates: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            one = _normalize_fact_dict(data)
            if one is not None:
                candidates.append(one)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    one = _normalize_fact_dict(item)
                    if one is not None:
                        candidates.append(one)
        for fact in candidates:
            ev = fact.get("evidence") if isinstance(fact.get("evidence"), dict) else {}
            if str(ev.get("dialogue_id") or dialogue_id) != dialogue_id:
                continue
            key = (
                str(fact.get("Atomic fact") or ""),
                str(ev.get("episode_id") or ""),
                str(ev.get("segment_id") or ""),
                str(fact.get("fact_id") or path.name),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(strip_embeddings(dict(fact)))
    return out


def _fact_merge_key(f: Dict[str, Any]) -> Tuple[str, str, str, str]:
    ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
    return (
        str(f.get("Atomic fact") or ""),
        str(ev.get("episode_id") or ""),
        str(ev.get("segment_id") or ""),
        str(ev.get("dialogue_id") or ""),
    )


def merge_file_facts_into_scene_layers(
    scene_layers: List[Dict[str, Any]],
    file_facts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Copy scene_layers and attach file-based facts to layers with matching ``scene_id``.
    Facts that cannot be placed are added in a final synthetic layer.
    """
    if not file_facts:
        return [dict(layer) for layer in scene_layers]
    layers_out: List[Dict[str, Any]] = []
    existing_keys: Set[Tuple[str, str, str, str]] = set()
    for layer in scene_layers:
        if not isinstance(layer, dict):
            continue
        dup = dict(layer)
        facts = dup.get("facts")
        if not isinstance(facts, list):
            facts = []
        else:
            facts = list(facts)
        dup["facts"] = facts
        for f in facts:
            if isinstance(f, dict):
                existing_keys.add(_fact_merge_key(f))
        layers_out.append(dup)

    unmatched: List[Dict[str, Any]] = []
    for f in file_facts:
        if not isinstance(f, dict):
            continue
        fsid = str(f.get("scene_id") or "").strip()
        placed = False
        if fsid:
            for layer in layers_out:
                if str(layer.get("scene_id") or "") == fsid:
                    lst = layer.setdefault("facts", [])
                    if not isinstance(lst, list):
                        lst = []
                        layer["facts"] = lst
                    k = _fact_merge_key(f)
                    if k not in existing_keys:
                        lst.append(f)
                        existing_keys.add(k)
                    placed = True
                    break
        if not placed:
            unmatched.append(f)

    if unmatched:
        lst_u: List[Dict[str, Any]] = []
        seen_u: Set[Tuple[str, str, str, str]] = set()
        for f in unmatched:
            k = _fact_merge_key(f)
            if k in seen_u:
                continue
            seen_u.add(k)
            if k not in existing_keys:
                lst_u.append(f)
        if lst_u:
            layers_out.append(
                {
                    "scene_id": "(facts_files)",
                    "_file": "facts",
                    "theme": "",
                    "diary": "",
                    "facts": lst_u,
                }
            )
    return layers_out


def narrative_sources_used(*, had_scene_layers: bool, file_facts: List[Dict[str, Any]]) -> str:
    ff = bool(file_facts)
    if had_scene_layers and ff:
        return "mixed"
    if had_scene_layers:
        return "scene"
    if ff:
        return "facts_files"
    return "none"


def strip_embeddings(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, child in value.items():
            if isinstance(key, str) and (key.endswith("_embedding") or key == "embedding"):
                continue
            out[key] = strip_embeddings(child)
        return out
    if isinstance(value, list):
        return [strip_embeddings(item) for item in value]
    return value


def count_scene_facts_light(path: Path) -> int:
    """Count facts without parsing embeddings (regex over file text)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return len(_ATOMIC_FACT_KEY_RE.findall(raw))


def peek_scene_light(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    match = _SCENE_ID_RE.search(raw)
    scene_id = match.group(1) if match else path.stem
    dialogue_ids = list(dict.fromkeys(_DIALOGUE_ID_RE.findall(raw)))
    return {
        "file": path.name,
        "scene_id": scene_id,
        "dialogue_ids": dialogue_ids,
        "fact_count": len(_ATOMIC_FACT_KEY_RE.findall(raw)),
    }


def iter_fact_json_files(facts_dir: Path) -> List[Path]:
    if not facts_dir.is_dir():
        return []
    return sorted(p for p in facts_dir.glob("*.json") if p.is_file())


def count_entity_statement_json(entity_statement_dir: Path) -> int:
    if not entity_statement_dir.is_dir():
        return 0
    return sum(1 for p in entity_statement_dir.rglob("*.json") if p.is_file())


def resolve_scene_file(scene_dir: Path, stem: str) -> Optional[Path]:
    raw = stem.strip()
    if not is_safe_scene_stem(raw):
        return None
    key = raw[6:] if raw.startswith("scene_") else raw
    candidates: List[Path] = []
    if key.isdigit():
        candidates.append(scene_dir / f"{int(key, 10):05d}.json")
    candidates.append(scene_dir / f"{key}.json")
    seen: set[str] = set()
    ordered: List[Path] = []
    for p in candidates:
        k = str(p.resolve())
        if k in seen:
            continue
        seen.add(k)
        ordered.append(p)
    for p in ordered:
        if p.is_file():
            return p
    return None


def iter_dialogue_json_files(dialogues_dir: Path) -> List[Path]:
    if not dialogues_dir.is_dir():
        return []
    files = sorted(dialogues_dir.rglob("*.json"))
    return [p for p in files if p.is_file()]


def summarize_dialogue_file(path: Path, *, rel_root: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        payload = {}
    dialogue_id = str(payload.get("dialogue_id") or path.stem)
    turns = payload.get("turns")
    turn_count = len(turns) if isinstance(turns, list) else 0
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    try:
        rel = str(path.resolve().relative_to(rel_root.resolve()))
    except ValueError:
        rel = path.name
    return {
        "dialogue_id": dialogue_id,
        "relative_path": rel.replace("\\", "/"),
        "meta": meta,
        "turn_count": turn_count,
    }


def load_json_if_exists(path: Path) -> Optional[Any]:
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_scene_summaries(scene_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, str]]]]:
    rows: List[Dict[str, Any]] = []
    by_dialogue: Dict[str, List[Dict[str, str]]] = {}
    if not scene_dir.is_dir():
        return rows, by_dialogue
    for path in sorted(scene_dir.glob("*.json")):
        if not path.is_file():
            continue
        summary = peek_scene_light(path)
        rows.append(summary)
        for dialogue_id in summary.get("dialogue_ids") or []:
            if not isinstance(dialogue_id, str) or not dialogue_id:
                continue
            by_dialogue.setdefault(dialogue_id, []).append(
                {
                    "file": summary["file"],
                    "scene_id": str(summary.get("scene_id") or ""),
                    "fact_count": int(summary.get("fact_count") or 0),
                }
            )
    return rows, by_dialogue


def _turn_span_bounds(seg: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(seg, dict):
        return None
    span = seg.get("turn_span")
    if not isinstance(span, list) or len(span) != 2:
        return None
    try:
        return int(span[0]), int(span[1])
    except (TypeError, ValueError):
        return None


def slice_turns_for_span(turns: Any, lo: int, hi: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(turns, list):
        return out
    for item in turns:
        if not isinstance(item, dict):
            continue
        tid = item.get("turn_id", item.get("turn"))
        try:
            tid_i = int(tid)
        except (TypeError, ValueError):
            continue
        if lo <= tid_i <= hi:
            out.append(item)
    out.sort(key=lambda x: int(x.get("turn_id", x.get("turn", 0)) or 0))
    return out


def _segments_from_facts_only(turns: List[Dict[str, Any]], facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_episode: Dict[str, Dict[str, Any]] = {}
    for fact in facts:
        ev = fact.get("evidence") if isinstance(fact.get("evidence"), dict) else {}
        epi = str(ev.get("episode_id") or "unknown_episode")
        seg = str(ev.get("segment_id") or "unknown_segment")
        if epi not in by_episode:
            by_episode[epi] = {
                "episode_id": epi,
                "topic": "",
                "turn_span": None,
                "segments": [],
                "orphan_facts": [],
            }
        ep_obj = by_episode[epi]
        span = ev.get("segment_turn_span")
        lo, hi = 0, max(0, len(turns) - 1)
        if isinstance(span, list) and len(span) == 2:
            try:
                lo, hi = int(span[0]), int(span[1])
            except (TypeError, ValueError):
                pass
        seg_bucket = None
        for s in ep_obj["segments"]:
            if isinstance(s, dict) and str(s.get("segment_id") or "") == seg:
                seg_bucket = s
                break
        if seg_bucket is None:
            seg_bucket = {
                "segment_id": seg,
                "topic": "",
                "turn_span": [lo, hi],
                "turns": slice_turns_for_span(turns, lo, hi) if hi >= lo else [],
                "facts": [],
            }
            ep_obj["segments"].append(seg_bucket)
        seg_bucket["facts"].append(fact)
    return list(by_episode.values())


def build_dialogue_narrative(
    dialogue: Dict[str, Any],
    episodes_doc: Optional[Dict[str, Any]],
    scene_layers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Join episodes/segments, dialogue turns, and scene facts. scene_layers: stripped scene dicts + _file."""
    dialogue_id = str(dialogue.get("dialogue_id") or "")
    scenes_out: List[Dict[str, Any]] = []
    all_unattached: List[Dict[str, Any]] = []

    for layer in scene_layers:
        if not isinstance(layer, dict):
            continue
        raw_facts = layer.get("facts") if isinstance(layer.get("facts"), list) else []
        facts = [strip_embeddings(f) for f in raw_facts if isinstance(f, dict)]
        facts = [
            f
            for f in facts
            if not dialogue_id or str((f.get("evidence") or {}).get("dialogue_id") or dialogue_id) == dialogue_id
        ]

        episodes_block, loose = _episodes_with_segments(
            dialogue=dialogue,
            episodes_doc=episodes_doc,
            facts=facts,
        )
        all_unattached.extend(loose)
        scenes_out.append(
            {
                "scene_id": str(layer.get("scene_id") or ""),
                "file": str(layer.get("_file") or ""),
                "theme": str(layer.get("theme") or ""),
                "diary": str(layer.get("diary") or ""),
                "facts_total_in_scene": len(facts),
                "episodes": episodes_block,
            }
        )

    return {
        "dialogue_id": dialogue_id,
        "scenes": scenes_out,
        "unattached_facts": all_unattached,
    }


def _episodes_with_segments(
    *,
    dialogue: Dict[str, Any],
    episodes_doc: Optional[Dict[str, Any]],
    facts: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    turns = dialogue.get("turns") if isinstance(dialogue.get("turns"), list) else []
    turns_list = [t for t in turns if isinstance(t, dict)]

    eps: List[Dict[str, Any]] = []
    if isinstance(episodes_doc, dict) and isinstance(episodes_doc.get("episodes"), list):
        eps = [e for e in episodes_doc["episodes"] if isinstance(e, dict)]

    if not eps:
        return _segments_from_facts_only(turns_list, facts), []

    placed: set[int] = set()
    result: List[Dict[str, Any]] = []

    for ep in eps:
        ep_id = str(ep.get("episode_id") or "")
        segments = ep.get("segments") if isinstance(ep.get("segments"), list) else []
        segments_out: List[Dict[str, Any]] = []

        for seg in segments:
            if not isinstance(seg, dict):
                continue
            sid = str(seg.get("segment_id") or "")
            bounds = _turn_span_bounds(seg)
            if bounds:
                lo, hi = bounds
                seg_turns = slice_turns_for_span(turns_list, lo, hi)
            else:
                seg_turns = []
            seg_facts: List[Dict[str, Any]] = []
            for f in facts:
                if id(f) in placed:
                    continue
                ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
                if str(ev.get("episode_id") or "") == ep_id and str(ev.get("segment_id") or "") == sid:
                    seg_facts.append(f)
                    placed.add(id(f))
            span_out: List[Any] = []
            if isinstance(seg.get("turn_span"), list):
                span_out = list(seg.get("turn_span") or [])
            elif bounds:
                span_out = [bounds[0], bounds[1]]
            segments_out.append(
                {
                    "segment_id": sid,
                    "topic": str(seg.get("topic") or ""),
                    "turn_span": span_out,
                    "turns": seg_turns,
                    "facts": seg_facts,
                }
            )

        orphan: List[Dict[str, Any]] = []
        for f in facts:
            if id(f) in placed:
                continue
            ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
            if str(ev.get("episode_id") or "") != ep_id:
                continue
            orphan.append(f)
            placed.add(id(f))

        result.append(
            {
                "episode_id": ep_id,
                "topic": str(ep.get("topic") or ""),
                "turn_span": ep.get("turn_span"),
                "segments": segments_out,
                "orphan_facts": orphan,
            }
        )

    loose = [f for f in facts if id(f) not in placed]
    return result, loose


def episode_situation_statistics_only(path: Path) -> Optional[Dict[str, Any]]:
    data = load_json_if_exists(path)
    if not isinstance(data, dict):
        return None
    stats = data.get("statistics")
    if isinstance(stats, dict):
        return dict(stats)
    return None
