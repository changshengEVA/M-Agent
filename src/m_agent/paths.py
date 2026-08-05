from __future__ import annotations

import os
import re
import sys
from pathlib import Path


_PACKAGE_PARENT = Path(__file__).resolve().parents[2]
_PROJECT_ROOT_OVERRIDE = str(os.getenv("M_AGENT_HOME", "")).strip()
_IS_SOURCE_TREE = (
    (_PACKAGE_PARENT / "pyproject.toml").is_file()
    and (_PACKAGE_PARENT / "src" / "m_agent").is_dir()
)
PROJECT_ROOT = (
    Path(_PROJECT_ROOT_OVERRIDE).expanduser().resolve()
    if _PROJECT_ROOT_OVERRIDE
    else _PACKAGE_PARENT
    if _IS_SOURCE_TREE
    else Path.cwd().resolve()
)
SRC_ROOT = PROJECT_ROOT / "src"

# Editable/source-tree installs use the repository config. A wheel installs the
# same public defaults under ``sys.prefix/share/m-agent/config``; user-specific
# config is intentionally never packaged.
_SOURCE_CONFIG_DIR = PROJECT_ROOT / "config"
_INSTALLED_CONFIG_DIR = Path(sys.prefix) / "share" / "m-agent" / "config"
_CONFIG_DIR_OVERRIDE = str(os.getenv("M_AGENT_CONFIG_DIR", "")).strip()
CONFIG_DIR = (
    Path(_CONFIG_DIR_OVERRIDE).expanduser().resolve()
    if _CONFIG_DIR_OVERRIDE
    else _SOURCE_CONFIG_DIR
    if (_SOURCE_CONFIG_DIR / "agents" / "chat" / "chat_controller.yaml").is_file()
    else _INSTALLED_CONFIG_DIR
)
DATA_DIR = PROJECT_ROOT / "data"
DOCS_DIR = PROJECT_ROOT / "docs"
EXAMPLES_DIR = PROJECT_ROOT / "examples"
EXPERIMENTS_DIR = PROJECT_ROOT / "experiments"
LOG_DIR = PROJECT_ROOT / "log"
MODEL_DIR = PROJECT_ROOT / "model"
CHECKPOINTS_DIR = PROJECT_ROOT / "checkpoints"
TOOLS_DIR = PROJECT_ROOT / "tools"
ENV_PATH = PROJECT_ROOT / ".env"


def resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def data_root_dir() -> Path:
    """Return the writable runtime data root.

    Installed configuration lives below ``sys.prefix`` and must remain
    read-only.  Mutable state therefore follows ``M_AGENT_DATA_DIR`` when set
    and otherwise uses ``<M_AGENT_HOME-or-cwd>/data``.
    """

    raw = str(os.getenv("M_AGENT_DATA_DIR", "")).strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DATA_DIR


def secrets_root_dir() -> Path:
    """Return the local, non-packaged root for runtime credentials/tokens."""

    raw = str(os.getenv("M_AGENT_SECRETS_DIR", "")).strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return PROJECT_ROOT / ".secrets"


def memory_root_dir() -> Path:
    """
    Resolve the base directory for MemoryCore workflow storage.

    Priority:
    1) M_AGENT_MEMORY_ROOT: full path to the directory that contains workflow subdirs
       (e.g. ".../full_model/GPT-4o-mini" so workflow_id "locomo/conv-48" becomes
       ".../full_model/GPT-4o-mini/locomo/conv-48").
    2) M_AGENT_DATA_DIR: full path to a data dir; memory root becomes "<data_dir>/memory".
    3) default: "<PROJECT_ROOT>/data/memory"
    """
    raw = str(os.getenv("M_AGENT_MEMORY_ROOT", "")).strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return data_root_dir() / "memory"


def memory_workflow_dir(workflow_id: str) -> Path:
    wid = str(workflow_id or "").strip()
    # Allow hierarchical workflow ids like "locomo/conv-48".
    wid = wid.strip("/\\")
    return memory_root_dir() / Path(wid)


def memory_stage_dir(workflow_id: str, stage_name: str) -> Path:
    return memory_workflow_dir(workflow_id) / str(stage_name)


def chat_user_slug(user_name: str, *, fallback: str = "user") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(user_name or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_.")
    return slug[:48] or fallback


def chat_memory_workflow_id(user_name: str) -> str:
    """Workflow id for per-user chat persistence (dialogues + episodic RAG)."""
    return f"chat-api/{chat_user_slug(user_name)}"


def chat_user_persistence_root(user_name: str) -> Path:
    """Per-user tree under ``data/memory/chat-api/<slug>/``."""
    root = memory_workflow_dir(chat_memory_workflow_id(user_name))
    root.mkdir(parents=True, exist_ok=True)
    return root


def chat_user_dialogues_dir(user_name: str) -> Path:
    root = chat_user_persistence_root(user_name) / "dialogues"
    root.mkdir(parents=True, exist_ok=True)
    return root


def chat_user_episodic_rag_paths(user_name: str) -> tuple[Path, str, Path]:
    """Return ``(storage_dir, workflow_id, index_root)`` for :class:`RagStore`.

    Index files live at ``<user_root>/episodic/{chunks.jsonl, embeddings.npy}``,
    sibling to ``dialogues/``.
    """
    user_root = chat_user_persistence_root(user_name)
    workflow_id = "episodic"
    index_root = user_root / workflow_id
    index_root.mkdir(parents=True, exist_ok=True)
    return user_root, workflow_id, index_root
