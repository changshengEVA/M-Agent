#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.memory_core.core.neo4j_store import Neo4jInitializationError, Neo4jStore


class _BrokenDriver:
    def verify_connectivity(self) -> None:
        raise ConnectionError("neo4j connection refused")


class Neo4jFailFastTest(unittest.TestCase):
    def tearDown(self) -> None:
        Neo4jStore._instance = None

    def test_connection_failure_raises_initialization_error_by_default(self) -> None:
        with patch.dict(os.environ, {"NEO4J_FAIL_FAST": "true"}, clear=False):
            with patch.object(
                Neo4jStore,
                "_load_config",
                return_value={
                    "url": "bolt://127.0.0.1:7687",
                    "user_name": "neo4j",
                    "password": "test-password",
                },
            ):
                with patch.object(Neo4jStore, "_create_driver", return_value=_BrokenDriver()):
                    with self.assertRaises(Neo4jInitializationError) as ctx:
                        Neo4jStore.instance()

        self.assertIn("protect memory alignment", str(ctx.exception))

    def test_connection_failure_can_explicitly_opt_out_of_fail_fast(self) -> None:
        with patch.dict(os.environ, {"NEO4J_FAIL_FAST": "false"}, clear=False):
            with patch.object(
                Neo4jStore,
                "_load_config",
                return_value={
                    "url": "bolt://127.0.0.1:7687",
                    "user_name": "neo4j",
                    "password": "test-password",
                },
            ):
                with patch.object(Neo4jStore, "_create_driver", return_value=_BrokenDriver()):
                    store = Neo4jStore.instance()

        self.assertFalse(store.available)
        self.assertIsNone(store.driver)


if __name__ == "__main__":
    unittest.main()
