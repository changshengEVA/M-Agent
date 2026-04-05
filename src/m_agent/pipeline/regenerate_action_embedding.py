#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regenerate scene fact embeddings from scene `facts`.

Prefer `Atomic fact` text. Legacy `actions` payloads remain supported for
backward compatibility.
"""

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from m_agent.memory.utils import get_output_path


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def init_embed_model(embed_provider: str = "bge") -> Optional[Callable[[Any], Any]]:
    provider = (embed_provider or "bge").strip().lower()
    try:
        if provider in {"alibaba", "aliyun", "dashscope"}:
            from m_agent.load_model.AlibabaEmbeddingCall import get_embed_model

            logger.info("Pre-initialize Alibaba embedding model")
            return get_embed_model()

        from m_agent.load_model.BGEcall import get_embed_model

        logger.info("Pre-initialize BGE embedding model")
        return get_embed_model()
    except Exception as exc:
        logger.warning("Embedding model init failed (%s): %s", provider, exc)
        return None


def build_action_embedding_text(actor: Any, action: Any) -> str:
    actor_text = str(actor or "").strip()
    action_text = str(action or "").strip()
    if actor_text and action_text:
        return f"{actor_text}: {action_text}"
    return action_text or actor_text


def build_fact_embedding_text(item: Any) -> str:
    if isinstance(item, dict):
        atomic_fact = str(
            item.get("Atomic fact")
            or item.get("atomic_fact")
            or item.get("fact")
            or item.get("fact_text")
            or ""
        ).strip()
        if atomic_fact:
            return atomic_fact
        return build_action_embedding_text(item.get("actor", ""), item.get("action", ""))
    return ""


def _is_valid_embedding(embedding: Any) -> bool:
    if not isinstance(embedding, list) or not embedding:
        return False
    return all(isinstance(v, (int, float)) for v in embedding)


def refresh_fact_embeddings_for_id(
    process_id: str,
    embed_model: Callable[[Any], Any],
    overwrite: bool,
) -> Dict[str, int]:
    scene_root = get_output_path(process_id, "scene")
    if not scene_root.exists():
        logger.error("Scene root does not exist: %s", scene_root)
        return {
            "scanned_files": 0,
            "updated_files": 0,
            "skipped_files": 0,
            "failed_files": 0,
            "scanned_facts": 0,
            "updated_facts": 0,
            "failed_facts": 0,
            "scanned_actions": 0,
            "updated_actions": 0,
            "failed_actions": 0,
        }

    def _scene_sort_key(path: Path) -> Tuple[int, Any]:
        try:
            return (0, int(path.stem))
        except ValueError:
            return (1, path.stem)

    stats = {
        "scanned_files": 0,
        "updated_files": 0,
        "skipped_files": 0,
        "failed_files": 0,
        "scanned_facts": 0,
        "updated_facts": 0,
        "failed_facts": 0,
        "scanned_actions": 0,
        "updated_actions": 0,
        "failed_actions": 0,
    }

    for scene_file in sorted(scene_root.glob("*.json"), key=_scene_sort_key):
        stats["scanned_files"] += 1

        try:
            with open(scene_file, "r", encoding="utf-8") as f:
                scene_data = json.load(f)
        except Exception as exc:
            logger.warning("Failed to load scene file: %s (%s)", scene_file, exc)
            stats["failed_files"] += 1
            continue

        fact_items = scene_data.get("facts", [])
        if not isinstance(fact_items, list):
            fact_items = scene_data.get("actions", [])
        if not isinstance(fact_items, list):
            stats["skipped_files"] += 1
            continue

        file_changed = False
        for item in fact_items:
            if not isinstance(item, dict):
                continue

            stats["scanned_facts"] += 1
            stats["scanned_actions"] += 1
            old_embedding = item.get("embedding")
            if not overwrite and _is_valid_embedding(old_embedding):
                continue

            embedding_input = build_fact_embedding_text(item)
            new_embedding: List[float] = []
            if embedding_input:
                try:
                    vector = embed_model(embedding_input)
                    if isinstance(vector, list):
                        new_embedding = [float(v) for v in vector if isinstance(v, (int, float))]
                except Exception as exc:
                    logger.warning(
                        "Embedding generation failed: scene=%s fact=%s error=%s",
                        scene_file.name,
                        embedding_input[:120],
                        exc,
                    )
                    stats["failed_facts"] += 1
                    stats["failed_actions"] += 1
                    continue

            if old_embedding != new_embedding:
                file_changed = True
            item["embedding"] = new_embedding
            stats["updated_facts"] += 1
            stats["updated_actions"] += 1

        if file_changed:
            try:
                with open(scene_file, "w", encoding="utf-8") as f:
                    json.dump(scene_data, f, ensure_ascii=False, indent=2)
                stats["updated_files"] += 1
            except Exception as exc:
                logger.warning("Failed to write scene file: %s (%s)", scene_file, exc)
                stats["failed_files"] += 1
        else:
            stats["skipped_files"] += 1

    return stats


def refresh_action_embeddings_for_id(
    process_id: str,
    embed_model: Callable[[Any], Any],
    overwrite: bool,
) -> Dict[str, int]:
    return refresh_fact_embeddings_for_id(
        process_id=process_id,
        embed_model=embed_model,
        overwrite=overwrite,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate fact embeddings for scene files")
    parser.add_argument("--id", type=str, required=True, help="Process ID under data/memory")
    parser.add_argument(
        "--embed-provider",
        type=str,
        default=os.getenv("EMBED_PROVIDER", "aliyun"),
        choices=["bge", "local", "alibaba", "aliyun", "dashscope"],
        help="Embedding provider",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Only generate embedding for facts without valid embedding",
    )
    args = parser.parse_args()

    embed_model = init_embed_model(args.embed_provider)
    if embed_model is None:
        logger.error("No embedding model available")
        return 1

    stats = refresh_fact_embeddings_for_id(
        process_id=args.id,
        embed_model=embed_model,
        overwrite=not args.only_missing,
    )

    logger.info(
        "Fact embedding regeneration complete for process_id=%s, stats=%s",
        args.id,
        stats,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

