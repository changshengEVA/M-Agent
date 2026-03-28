#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared Neo4j store utilities used directly by core."""

from __future__ import annotations

import logging
import os
import re
from threading import Lock
from typing import Any, Dict, Optional

try:
    import yaml
except Exception:
    yaml = None

from m_agent.paths import CONFIG_DIR

logger = logging.getLogger(__name__)


class Neo4jInitializationError(RuntimeError):
    """Raised when Neo4j cannot be initialized in fail-fast mode."""


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
        self.fail_fast = self._is_fail_fast_enabled()
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
            self._handle_init_failure(
                "Neo4j config missing url/user_name; aborting to protect memory alignment."
            )
            return

        try:
            self.driver = self._create_driver(self.url)
            self.driver.verify_connectivity()
            self.available = True
            self._ensure_schema()
            logger.info("Neo4j connected: %s", self.url)
        except Exception as exc:
            if self._maybe_switch_to_direct(exc):
                try:
                    self.driver = self._create_driver(self.url)
                    self.driver.verify_connectivity()
                    self.available = True
                    self._ensure_schema()
                    logger.info("Neo4j connected (direct mode): %s", self.url)
                    return
                except Exception as retry_exc:
                    exc = retry_exc
            self.available = False
            self.driver = None
            self._handle_init_failure(
                f"Neo4j unavailable ({exc}); aborting to protect memory alignment.",
                exc,
            )

    @staticmethod
    def _is_fail_fast_enabled() -> bool:
        raw = str(os.getenv("NEO4J_FAIL_FAST", "true")).strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def _handle_init_failure(self, message: str, exc: Optional[Exception] = None) -> None:
        self.available = False
        self.driver = None
        if self.fail_fast:
            logger.error(message)
            if exc is None:
                raise Neo4jInitializationError(message)
            raise Neo4jInitializationError(message) from exc
        logger.warning("%s", message)

    def _create_driver(self, url: str):
        from neo4j import GraphDatabase

        return GraphDatabase.driver(url, auth=(self.user, self.password))

    @staticmethod
    def _is_routing_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "routing information" in msg or "unable to retrieve routing" in msg

    @staticmethod
    def _to_direct_url(url: str) -> Optional[str]:
        text = str(url or "").strip()
        mappings = [
            ("neo4j+ssc://", "bolt+ssc://"),
            ("neo4j+s://", "bolt+s://"),
            ("neo4j://", "bolt://"),
        ]
        for src, dst in mappings:
            if text.startswith(src):
                return dst + text[len(src) :]
        return None

    def _maybe_switch_to_direct(self, exc: Exception) -> bool:
        if not self._is_routing_error(exc):
            return False
        direct_url = self._to_direct_url(self.url)
        if not direct_url or direct_url == self.url:
            return False
        old_url = self.url
        self.url = direct_url
        logger.warning(
            "Neo4j routing unavailable on '%s', fallback to direct URL '%s'.",
            old_url,
            direct_url,
        )
        return True

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

        cfg_path = CONFIG_DIR / "neo4j.yaml"
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

    def _ensure_schema_in_db(self, database: Optional[str]) -> bool:
        if not self.available or self.driver is None:
            return False
        constraints = [
            "DROP CONSTRAINT entity_workflow_id_unique IF EXISTS",
            "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
        ]
        session_kwargs: Dict[str, Any] = {}
        db = self._sanitize_db_name(database or "")
        if db:
            session_kwargs["database"] = db

        for attempt in range(2):
            try:
                with self.driver.session(**session_kwargs) as session:
                    for query in constraints:
                        session.run(query)
                return True
            except Exception as exc:
                if attempt == 0 and self._maybe_switch_to_direct(exc):
                    self.driver = self._create_driver(self.url)
                    self.driver.verify_connectivity()
                    continue
                target = db if db else "<default>"
                logger.warning("Neo4j schema ensure failed for database '%s': %s", target, exc)
                return False
        return False

    @staticmethod
    def _sanitize_db_name(name: str) -> str:
        lowered = str(name or "").strip().lower()
        # Neo4j database names accept ascii letters, numbers, dots and dashes.
        lowered = lowered.replace("_", "-")
        sanitized = re.sub(r"[^a-z0-9.\-]", "-", lowered)
        sanitized = re.sub(r"[-.]{2,}", "-", sanitized).strip("-.")
        return sanitized

    def resolve_database(self, workflow_id: str) -> Optional[str]:
        template = str(
            os.getenv(
                "NEO4J_DATABASE_TEMPLATE",
                self.config.get("database_template", "wf-{workflow_id}"),
            )
        )
        database = self._sanitize_db_name(template.format(workflow_id=workflow_id))
        if database:
            return database
        fallback = self._sanitize_db_name(f"wf-{workflow_id}") or "wf-default"
        return fallback

    @staticmethod
    def _is_create_database_unsupported(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "unsupportedadministrationcommand" in msg
            or "unsupported administration command" in msg
            or ("create database" in msg and "community" in msg)
            or ("create database" in msg and "not supported" in msg)
        )

    def ensure_database(self, database: Optional[str]) -> Optional[str]:
        if not self.available or self.driver is None:
            return None
        db = self._sanitize_db_name(database or "")
        if not db:
            return None

        for attempt in range(2):
            try:
                with self.driver.session(database="system") as session:
                    session.run(f"CREATE DATABASE `{db}` IF NOT EXISTS")
                self._ensure_schema_in_db(db)
                return db
            except Exception as exc:
                if attempt == 0 and self._maybe_switch_to_direct(exc):
                    self.driver = self._create_driver(self.url)
                    self.driver.verify_connectivity()
                    continue
                if self._is_create_database_unsupported(exc):
                    logger.warning(
                        "CREATE DATABASE is unsupported on this Neo4j server; fallback to default database."
                    )
                    self._ensure_schema_in_db(None)
                    return None
                logger.warning("Ensure database failed for '%s': %s", db, exc)
                # Keep system running: fallback to default database.
                self._ensure_schema_in_db(None)
                return None
        return None

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

        for attempt in range(2):
            try:
                with self.driver.session(**session_kwargs) as session:
                    if write:
                        return session.execute_write(_tx, query, params or {})
                    return session.execute_read(_tx, query, params or {})
            except Exception as exc:
                if attempt == 0 and self._maybe_switch_to_direct(exc):
                    self.driver = self._create_driver(self.url)
                    self.driver.verify_connectivity()
                    continue
                raise
