#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.memory_core.services_bank.entity_profile_sys.service import (
    ENTITY_PROFILE_RESET_CONFIRM_TOKEN,
    EntityProfileService,
)
from memory.memory_core.workflow.build import extract_fact_entities as fact_entity_workflow


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


class EntityProfileRebuildGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_service_memory_root(self) -> tuple[Path, Path]:
        memory_root = self.temp_root / "memory_case"
        (memory_root / "facts").mkdir(parents=True, exist_ok=True)
        (memory_root / "local_store").mkdir(parents=True, exist_ok=True)
        prompt_path = memory_root / "facts_filter.yaml"
        prompt_path.write_text("fact_filter_v1: |\n  {fact}\n", encoding="utf-8")
        return memory_root, prompt_path

    def _build_service(self, llm_responses, memory_root: Path, prompt_path: Path) -> EntityProfileService:
        return EntityProfileService(
            llm_func=SequenceLLM(llm_responses),
            embed_func=stable_embed,
            memory_root=str(memory_root),
            prompt_path=str(prompt_path),
            enable_summary_llm=False,
            enable_progress=False,
            auto_align_on_init=False,
            rebuild_checkpoint_every=1,
        )

    def test_system_initialized_not_subscribed_by_default(self) -> None:
        memory_root, prompt_path = self._build_service_memory_root()
        service = self._build_service([], memory_root=memory_root, prompt_path=prompt_path)
        subscribed = service.get_subscribed_events()
        self.assertNotIn("SYSTEM_INITIALIZED", subscribed)

    def test_extract_fact_entities_skips_unchanged_fact_file_rewrite(self) -> None:
        workflow_id = "demo"
        project_root = self.temp_root / "repo_root"
        memory_root = project_root / "data" / "memory" / workflow_id
        scene_root = memory_root / "scene"
        scene_root.mkdir(parents=True, exist_ok=True)

        scene_payload = {
            "scene_id": "scene_00001",
            "facts": [
                {
                    "Atomic fact": "Alice likes apples.",
                    "embedding": [1.0, 2.0],
                    "evidence": {
                        "dialogue_id": "dialogue_1",
                        "episode_id": "episode_1",
                    },
                }
            ],
            "source": {
                "episodes": [
                    {
                        "dialogue_id": "dialogue_1",
                        "episode_id": "episode_1",
                    }
                ]
            },
        }
        self._write_json(scene_root / "00001.json", scene_payload)

        with mock.patch.object(fact_entity_workflow, "PROJECT_ROOT", project_root):
            first = fact_entity_workflow.scan_and_extract_fact_entities(
                workflow_id=workflow_id,
                force_update=False,
                use_tqdm=False,
                llm_model=SequenceLLM(['{"main_entity":"Alice","other_entities":["apples"]}']),
            )
            fact_path = memory_root / "facts" / "00001_0001.json"
            before_text = fact_path.read_text(encoding="utf-8")
            before_mtime = fact_path.stat().st_mtime_ns

            second = fact_entity_workflow.scan_and_extract_fact_entities(
                workflow_id=workflow_id,
                force_update=False,
                use_tqdm=False,
                llm_model=SequenceLLM([]),
            )

        self.assertEqual(first["fact_files_written"], 1)
        self.assertEqual(second["fact_files_written"], 0)
        self.assertEqual(fact_path.read_text(encoding="utf-8"), before_text)
        self.assertEqual(fact_path.stat().st_mtime_ns, before_mtime)

    def test_align_ignores_fact_file_mtime_only_change(self) -> None:
        memory_root, prompt_path = self._build_service_memory_root()
        fact_payload = {
            "fact_id": "fact_1",
            "Atomic fact": "Alice likes apples.",
            "main_entity": "Alice",
            "entity_UID": "alice",
            "evidence": {
                "dialogue_id": "dialogue_1",
                "episode_id": "episode_1",
            },
        }
        fact_path = memory_root / "facts" / "fact_1.json"
        self._write_json(fact_path, fact_payload)

        service = self._build_service(
            ['{"event_available": false, "attribute_available": false}'],
            memory_root=memory_root,
            prompt_path=prompt_path,
        )
        first = service.align_with_master_facts(force_rebuild=True)
        self.assertEqual(first["mode"], "rebuild")
        self.assertEqual(first["facts_processed"], 1)

        time.sleep(0.02)
        self._write_json(fact_path, fact_payload)

        second = service.align_with_master_facts(force_rebuild=False)
        self.assertEqual(second["mode"], "incremental")
        self.assertEqual(second["facts_processed"], 0)
        self.assertEqual(second["facts_changed"], 0)

    def test_align_reports_drift_and_only_processes_new_facts(self) -> None:
        memory_root, prompt_path = self._build_service_memory_root()
        fact_1 = {
            "fact_id": "fact_1",
            "Atomic fact": "Alice likes apples.",
            "main_entity": "Alice",
            "entity_UID": "alice",
            "evidence": {
                "dialogue_id": "dialogue_1",
                "episode_id": "episode_1",
            },
        }
        self._write_json(memory_root / "facts" / "fact_1.json", fact_1)

        service = self._build_service(
            ['{"event_available": false, "attribute_available": false}'],
            memory_root=memory_root,
            prompt_path=prompt_path,
        )
        initial = service.align_with_master_facts(force_rebuild=True)
        self.assertEqual(initial["mode"], "rebuild")
        self.assertEqual(initial["facts_processed"], 1)

        changed_fact_1 = dict(fact_1)
        changed_fact_1["Atomic fact"] = "Alice likes green apples."
        fact_2 = {
            "fact_id": "fact_2",
            "Atomic fact": "Alice runs every morning.",
            "main_entity": "Alice",
            "entity_UID": "alice",
            "evidence": {
                "dialogue_id": "dialogue_1",
                "episode_id": "episode_1",
            },
        }
        self._write_json(memory_root / "facts" / "fact_1.json", changed_fact_1)
        self._write_json(memory_root / "facts" / "fact_2.json", fact_2)

        service.llm_func = SequenceLLM(['{"event_available": false, "attribute_available": false}'])
        incremental = service.align_with_master_facts(force_rebuild=False)

        self.assertEqual(incremental["mode"], "incremental")
        self.assertEqual(incremental["facts_processed"], 1)
        self.assertEqual(incremental["facts_new"], 1)
        self.assertEqual(incremental["facts_changed"], 1)
        self.assertEqual(incremental["facts_removed"], 0)
        self.assertTrue(incremental["reset_required"])
        self.assertTrue(incremental["fact_drift"]["drift_detected"])

        local_state = json.loads((memory_root / "local_store" / "facts_situation.json").read_text(encoding="utf-8"))
        self.assertEqual(len(local_state["facts"]), 2)
        self.assertEqual(local_state["metadata"]["fact_drift"]["facts_changed"], 1)

    def test_sample_rebuild_uses_isolated_output_dir(self) -> None:
        memory_root, prompt_path = self._build_service_memory_root()
        for idx in range(100):
            fact_payload = {
                "fact_id": f"fact_{idx:04d}",
                "Atomic fact": f"Alice mentions item {idx}.",
                "main_entity": "Alice",
                "entity_UID": "alice",
                "evidence": {
                    "dialogue_id": "dialogue_1",
                    "episode_id": "episode_1",
                },
            }
            self._write_json(memory_root / "facts" / f"fact_{idx:04d}.json", fact_payload)

        service = self._build_service(
            ['{"event_available": false, "attribute_available": false}'],
            memory_root=memory_root,
            prompt_path=prompt_path,
        )
        result = service.rebuild_from_sampled_facts(
            sample_ratio=0.01,
            sample_seed=7,
            output_tag="smoke",
        )

        self.assertTrue(result["sample_mode"])
        self.assertEqual(result["sampled_fact_count"], 1)
        self.assertEqual(result["facts_processed"], 1)
        self.assertIn("entity_profile_samples", result["sample_profile_data_path"])
        self.assertTrue(Path(result["sample_profile_data_path"]).exists())
        self.assertTrue(Path(result["sample_facts_situation_file"]).exists())
        self.assertEqual(list((memory_root / "local_store" / "entity_profile").glob("*.json")), [])

    def test_false_positive_checkpoint_recovers_without_resuming_full_rebuild(self) -> None:
        memory_root, prompt_path = self._build_service_memory_root()
        for idx in range(3):
            fact_payload = {
                "fact_id": f"fact_{idx}",
                "Atomic fact": f"Alice remembers event {idx}.",
                "main_entity": "Alice",
                "entity_UID": "alice",
                "evidence": {
                    "dialogue_id": "dialogue_1",
                    "episode_id": "episode_1",
                },
            }
            self._write_json(memory_root / "facts" / f"fact_{idx}.json", fact_payload)

        service = self._build_service(
            [
                '{"event_available": false, "attribute_available": false}',
                '{"event_available": false, "attribute_available": false}',
                '{"event_available": false, "attribute_available": false}',
            ],
            memory_root=memory_root,
            prompt_path=prompt_path,
        )
        full = service.align_with_master_facts(force_rebuild=True)
        self.assertEqual(full["facts_processed"], 3)

        local_state_path = memory_root / "local_store" / "facts_situation.json"
        local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
        partial_fact_id = sorted(local_state["facts"].keys())[0]
        partial_state = {
            "workflow_id": memory_root.name,
            "summary": {},
            "facts": {
                partial_fact_id: local_state["facts"][partial_fact_id],
            },
            "metadata": {
                "rebuild_reason": "changed=3",
                "rebuild_checkpoint": {
                    "status": "in_progress",
                    "reason": "changed=3",
                    "started_at": "2026-03-24T00:00:00Z",
                    "updated_at": "2026-03-24T00:01:00Z",
                    "total_facts": 3,
                    "processed_facts": 1,
                    "failed_facts": 0,
                    "remaining_facts": 2,
                    "checkpoint_every": 1,
                    "checkpoint_dir": str(memory_root / "local_store" / "entity_profile_rebuild_checkpoint"),
                    "resumed_from_checkpoint": False,
                },
            },
        }
        self._write_json(local_state_path, partial_state)

        recovered = service.align_with_master_facts(force_rebuild=False)
        self.assertEqual(recovered["mode"], "checkpoint_recovered")
        self.assertEqual(recovered["facts_processed"], 0)

        repaired_state = json.loads(local_state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(repaired_state["facts"]), 3)
        self.assertEqual(repaired_state["metadata"]["rebuild_checkpoint"]["status"], "completed")

    def test_active_checkpoint_blocks_auto_resume_until_reset(self) -> None:
        memory_root, prompt_path = self._build_service_memory_root()
        fact_payload = {
            "fact_id": "fact_1",
            "Atomic fact": "Alice likes apples.",
            "main_entity": "Alice",
            "entity_UID": "alice",
            "evidence": {
                "dialogue_id": "dialogue_1",
                "episode_id": "episode_1",
            },
        }
        self._write_json(memory_root / "facts" / "fact_1.json", fact_payload)
        local_state_path = memory_root / "local_store" / "facts_situation.json"
        self._write_json(
            local_state_path,
            {
                "workflow_id": memory_root.name,
                "summary": {},
                "facts": {},
                "metadata": {
                    "rebuild_checkpoint": {
                        "status": "in_progress",
                        "reason": "manual_test_checkpoint",
                        "started_at": "2026-03-25T00:00:00Z",
                        "updated_at": "2026-03-25T00:01:00Z",
                        "total_facts": 1,
                        "processed_facts": 0,
                        "failed_facts": 0,
                        "remaining_facts": 1,
                        "checkpoint_dir": str(memory_root / "local_store" / "entity_profile_rebuild_checkpoint"),
                    }
                },
            },
        )

        service = self._build_service([], memory_root=memory_root, prompt_path=prompt_path)
        blocked = service.align_with_master_facts(force_rebuild=False)

        self.assertEqual(blocked["mode"], "checkpoint_blocked")
        self.assertTrue(blocked["reset_required"])
        self.assertEqual(blocked["checkpoint_reason"], "manual_test_checkpoint")

    def test_reset_alignment_state_requires_token_and_clears_outputs(self) -> None:
        memory_root, prompt_path = self._build_service_memory_root()
        fact_payload = {
            "fact_id": "fact_1",
            "Atomic fact": "Alice likes apples.",
            "main_entity": "Alice",
            "entity_UID": "alice",
            "evidence": {
                "dialogue_id": "dialogue_1",
                "episode_id": "episode_1",
            },
        }
        self._write_json(memory_root / "facts" / "fact_1.json", fact_payload)

        service = self._build_service(
            ['{"event_available": false, "attribute_available": false}'],
            memory_root=memory_root,
            prompt_path=prompt_path,
        )
        built = service.align_with_master_facts(force_rebuild=True)
        self.assertEqual(built["facts_processed"], 1)
        self.assertEqual(len(list((memory_root / "local_store" / "entity_profile").glob("*.json"))), 1)

        with self.assertRaises(ValueError):
            service.reset_alignment_state(confirm_token="WRONG_TOKEN")

        reset_result = service.reset_alignment_state(confirm_token=ENTITY_PROFILE_RESET_CONFIRM_TOKEN)
        self.assertEqual(reset_result["mode"], "manual_reset")
        self.assertEqual(len(list((memory_root / "local_store" / "entity_profile").glob("*.json"))), 0)

        local_state = json.loads((memory_root / "local_store" / "facts_situation.json").read_text(encoding="utf-8"))
        self.assertEqual(local_state["facts"], {})
        self.assertEqual(local_state["metadata"]["rebuild_checkpoint"]["status"], "reset")


if __name__ == "__main__":
    unittest.main()
