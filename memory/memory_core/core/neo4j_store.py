#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared Neo4j store utilities used directly by core."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

try:
    import yaml
except Exception:
    yaml = None

logger = logging.getLogger(__name__)


class Neo4jStore:
    """Singleton Neo4j driver holder with local-config bootstrap."""

    _instance: Optional["Neo4jStore"] = None
    _lock: Lock = Lock()

    def __init__(self) -> None:
        self.config: Dict[str, Any] = {}
        self.url = ""
        self.user = ""
        self.password = ""
        self.driver = None
        self.available = False
        self._init_driver()

    @classmethod
    def instance(cls) -> "Neo4jStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _init_driver(self) -> None:
        cfg = self._load_config()
        self.config = cfg
        self.url = str(cfg.get("url") or "").strip()
        self.user = str(cfg.get("user_name") or cfg.get("username") or "").strip()
        self.password = str(cfg.get("password") or "").strip()

        if not self.url or not self.user:
            logger.warning("Neo4j config missing url/user_name, core runs in disabled mode.")
            return

        try:
            from neo4j import GraphDatabase

            self.driver = GraphDatabase.driver(self.url, auth=(self.user, self.password))
            self.driver.verify_connectivity()
            self.available = True
            self._ensure_schema()
            logger.info("Neo4j connected: %s", self.url)
        except Exception as exc:
            self.available = False
            self.driver = None
            logger.warning("Neo4j unavailable (%s). Core graph ops become no-op/fail-safe.", exc)

    def _load_config(self) -> Dict[str, Any]:
        env_url = os.getenv("NEO4J_URL")
        env_user = os.getenv("NEO4J_USER")
        env_pass = os.getenv("NEO4J_PASSWORD")
        if env_url and env_user:
            return {
                "url": env_url,
                "user_name": env_user,
                "password": env_pass or "",
            }

        project_root = Path(__file__).resolve().parents[3]
        cfg_path = project_root / "config" / "neo4j.yaml"
        if not cfg_path.exists():
            return {}

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = f.read()
            if yaml is not None:
                data = yaml.safe_load(raw) or {}
                if isinstance(data, dict):
                    return data
            data: Dict[str, Any] = {}
            for line in raw.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or ":" not in stripped:
                    continue
                key, value = stripped.split(":", 1)
                data[key.strip()] = value.strip().strip("'\"")
            return data
        except Exception as exc:
            logger.warning("Load neo4j config failed: %s", exc)
        return {}

    def _ensure_schema(self) -> None:
        if not self.available or self.driver is None:
            return
        self._ensure_schema_in_db(None)

    def _ensure_schema_in_db(self, database: Optional[str]) -> None:
        if not self.available or self.driver is None:
            return
        constraints = [
            "DROP CONSTRAINT entity_workflow_id_unique IF EXISTS",
            "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
        ]
        try:
            session_kwargs: Dict[str, Any] = {}
            db = self._sanitize_db_name(database or "")
            if db:
                session_kwargs["database"] = db
            with self.driver.session(**session_kwargs) as session:
                for query in constraints:
                    session.run(query)
        except Exception as exc:
            target = db if db else "<default>"
            logger.warning("Neo4j schema ensure failed for database '%s': %s", target, exc)

    @staticmethod
    def _sanitize_db_name(name: str) -> str:
        lowered = str(name or "").strip().lower()
        return re.sub(r"[^a-z0-9_]", "_", lowered)

    def resolve_database(self, workflow_id: str) -> Optional[str]:
        template = str(
            os.getenv(
                "NEO4J_DATABASE_TEMPLATE",
                self.config.get("database_template", "wf_{workflow_id}"),
            )
        )
        return self._sanitize_db_name(template.format(workflow_id=workflow_id))

    def ensure_database(self, database: Optional[str]) -> None:
        if not self.available or self.driver is None:
            return
        db = self._sanitize_db_name(database or "")
        if not db:
            return
        try:
            with self.driver.session(database="system") as session:
                session.run(f"CREATE DATABASE `{db}` IF NOT EXISTS")
            self._ensure_schema_in_db(db)
        except Exception as exc:
            logger.warning("Ensure database failed for '%s': %s", db, exc)

    def run(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        write: bool = False,
        database: Optional[str] = None,
    ):
        if not self.available or self.driver is None:
            raise RuntimeError("Neo4j is not available")

        def _tx(tx, q, p):
            return list(tx.run(q, p or {}))

        session_kwargs: Dict[str, Any] = {}
        db = self._sanitize_db_name(database or "")
        if db:
            session_kwargs["database"] = db

        with self.driver.session(**session_kwargs) as session:
            if write:
                return session.execute_write(_tx, query, params or {})
            return session.execute_read(_tx, query, params or {})
