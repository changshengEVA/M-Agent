"""Episodic loader uses RAG backend from rag_default.yaml."""
from __future__ import annotations

from pathlib import Path

from m_agent.systems.episodic import load_episodic_system
from m_agent.systems.episodic.default.rag_backend import SimpleRagEpisodicBackend


def test_load_rag_default_yaml() -> None:
    root = Path(__file__).resolve().parents[3]
    yaml_path = root / "config" / "systems" / "episodic" / "rag_default.yaml"
    system = load_episodic_system(yaml_path)
    assert isinstance(system.backend, SimpleRagEpisodicBackend)
    assert system.backend.min_score == 0.2
