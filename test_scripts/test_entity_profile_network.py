#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.memory_core.services_bank.entity_profile_sys.errors import EntityProfileNetworkError
from memory.memory_core.services_bank.entity_profile_sys.service import EntityProfileService


class SequenceLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self._index = 0

    def __call__(self, prompt: str) -> str:
        if self._index >= len(self._responses):
            raise AssertionError(f"unexpected extra llm call #{self._index + 1}: {prompt[:120]}")
        current = self._responses[self._index]
        self._index += 1
        if isinstance(current, BaseException):
            raise current
        return str(current)


def stable_embed(text: str):
    cleaned = str(text or "").strip()
    base = float(len(cleaned) or 1)
    return [base, 1.0, 0.5]


class EntityProfileNetworkRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory_case"
        (self.memory_root / "facts").mkdir(parents=True, exist_ok=True)
        (self.memory_root / "local_store").mkdir(parents=True, exist_ok=True)
        self.prompt_path = self.memory_root / "facts_filter.yaml"
        self.prompt_path.write_text("fact_filter_v1: |\n  {fact}\n", encoding="utf-8")

        fact_payload = {
            "fact_id": "fact_1",
            "Atomic fact": "Alice likes apples and goes jogging every morning.",
            "main_entity": "Alice",
            "entity_UID": "alice",
            "evidence": {
                "dialogue_id": "dialogue_1",
                "episode_id": "episode_1",
            },
        }
        (self.memory_root / "facts" / "fact_1.json").write_text(
            json.dumps(fact_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _build_service(self, llm_responses) -> EntityProfileService:
        return EntityProfileService(
            llm_func=SequenceLLM(llm_responses),
            embed_func=stable_embed,
            memory_root=str(self.memory_root),
            prompt_path=str(self.prompt_path),
            enable_summary_llm=False,
            enable_progress=False,
            auto_align_on_init=False,
            rebuild_checkpoint_every=1,
        )

    def test_network_error_interrupts_rebuild_and_marks_checkpoint(self) -> None:
        service = self._build_service(
            [ConnectionError("network is unreachable while calling llm")]
        )

        with self.assertRaises(EntityProfileNetworkError):
            service.align_with_master_facts(force_rebuild=True)

        local_state_path = self.memory_root / "local_store" / "facts_situation.json"
        self.assertTrue(local_state_path.exists())
        local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
        checkpoint = local_state.get("metadata", {}).get("rebuild_checkpoint", {})
        self.assertEqual(checkpoint.get("status"), "interrupted")
        self.assertIn("network", str(checkpoint.get("error", "")).lower())
        self.assertEqual(local_state.get("facts"), {})
        self.assertTrue((self.memory_root / "local_store" / "entity_profile_rebuild_checkpoint").exists())

    def test_mid_fact_network_error_rolls_back_partial_profile_changes(self) -> None:
        service = self._build_service(
            [
                '{"event_available": true, "attribute_available": true}',
                '{"attributes":[{"field":"like","content":["apple"]}]}',
                ConnectionError("network is unreachable while extracting event"),
            ]
        )

        with self.assertRaises(EntityProfileNetworkError):
            service.align_with_master_facts(force_rebuild=True)

        self.assertIsNone(service.entity_profile_library.get_entity("alice"))
        local_state_path = self.memory_root / "local_store" / "facts_situation.json"
        local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
        self.assertEqual(local_state.get("facts"), {})
        profile_dir = self.memory_root / "local_store" / "entity_profile"
        self.assertEqual(list(profile_dir.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
