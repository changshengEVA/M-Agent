from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from m_agent.config_paths import (
    AGENT_RUNTIME_PROMPT_CONFIG_PATH,
    DEFAULT_MEMORY_CORE_CONFIG_PATH,
    MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH,
    resolve_related_config_path,
)
from m_agent.load_model.AlibabaEmbeddingCall import get_embed_model as get_alibaba_embed_model
from m_agent.load_model.BGEcall import get_embed_model as get_local_embed_model
from m_agent.load_model.OpenAIcall import get_llm
from m_agent.memory.memory_core.memory_system import MemoryCore
from m_agent.prompt_utils import (
    load_resolved_prompt_config,
    normalize_prompt_language,
    render_prompt_template,
)

logger = logging.getLogger(__name__)


class MemoryAgentConfigMixin:
    @staticmethod
    def _load_config(path: Path) -> Dict[str, Any]:
        """加载 agent 配置，并合并 base_config 链路。"""
        def _load_raw_config(config_path: Path) -> Dict[str, Any]:
            """读取单个 YAML 配置文件。"""
            if not config_path.exists():
                raise FileNotFoundError(f"Agent config not found: {config_path}")
            with open(config_path, "r", encoding="utf-8") as f:
                payload = yaml.safe_load(f) or {}
            if not isinstance(payload, dict):
                raise ValueError(f"Agent config must be a dict: {config_path}")
            return payload

        def _normalize_path_fields(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
            """将配置中的路径字段规范化为绝对路径。"""
            normalized = dict(payload)
            for key in ("memory_core_config_path", "runtime_prompt_config_path"):
                raw_value = normalized.get(key)
                if not isinstance(raw_value, str) or not raw_value.strip():
                    continue
                raw_path = Path(raw_value.strip())
                if raw_path.is_absolute():
                    normalized[key] = str(raw_path)
                    continue
                normalized[key] = str((config_path.parent / raw_path).resolve())
            return normalized

        def _merge_with_base(config_path: Path, visited: set[Path]) -> Dict[str, Any]:
            """递归合并 base_config_path 对应的配置。"""
            config = _normalize_path_fields(config_path, _load_raw_config(config_path))
            raw_base_path = config.get("base_config_path")
            if not isinstance(raw_base_path, str) or not raw_base_path.strip():
                return config

            base_path = resolve_related_config_path(config_path, raw_base_path).resolve()
            if base_path in visited:
                chain = " -> ".join(str(item) for item in list(visited) + [base_path])
                raise ValueError(f"Detected cyclic base_config_path chain: {chain}")

            base_config = _merge_with_base(base_path, visited | {base_path})
            merged = dict(base_config)
            for key, value in config.items():
                if key == "base_config_path":
                    continue
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged_value = dict(merged.get(key) or {})
                    merged_value.update(value)
                    merged[key] = merged_value
                    continue
                merged[key] = value
            return merged

        resolved_path = path.resolve()
        config = _merge_with_base(resolved_path, {resolved_path})

        if not isinstance(config.get("memory_core_config_path"), str) or not str(
            config.get("memory_core_config_path")
        ).strip():
            raise ValueError("`memory_core_config_path` is required in agent config")
        return config
    def _resolve_related_path(self, raw_path: Any) -> Path:
        """解析与 agent 相关的文件路径。"""
        return resolve_related_config_path(
            self.config_path,
            raw_path,
            default_path=DEFAULT_MEMORY_CORE_CONFIG_PATH,
        )
    def _resolve_runtime_prompt_config_path(self, raw_path: Any) -> Path:
        """解析 runtime prompt 配置路径。"""
        return resolve_related_config_path(
            self.config_path,
            raw_path,
            default_path=AGENT_RUNTIME_PROMPT_CONFIG_PATH,
        )
    def _load_runtime_prompts(self, path: Path) -> Dict[str, Any]:
        """加载 memory_agent 命名空间下的 prompts。"""
        config = load_resolved_prompt_config(path, language=self.prompt_language)
        prompts = config.get("memory_agent")
        if not isinstance(prompts, dict):
            raise ValueError(f"`memory_agent` prompt namespace is required in runtime prompt config: {path}")
        return prompts
    def _get_runtime_prompt_text(self, *keys: str) -> str:
        """根据 key 链读取单条 prompt 文本。"""
        node: Any = self.runtime_prompts
        full_key = ".".join(keys)
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                raise ValueError(
                    f"Runtime prompt '{full_key}' is missing in config: {self.runtime_prompt_config_path}"
                )
            node = node[key]
        if not isinstance(node, str) or not node.strip():
            raise ValueError(
                f"Runtime prompt '{full_key}' is empty in config: {self.runtime_prompt_config_path}"
            )
        return node.strip()
    def _render_runtime_prompt(self, *keys: str, replacements: Dict[str, Any]) -> str:
        """渲染带变量替换的 runtime prompt。"""
        template = self._get_runtime_prompt_text(*keys)
        return render_prompt_template(template, replacements).strip()
    @staticmethod
    def _load_memory_core_config(path: Path) -> Dict[str, Any]:
        """加载 MemoryCore 配置。"""
        if not path.exists():
            raise FileNotFoundError(f"MemoryCore config not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        if not isinstance(config, dict):
            raise ValueError(f"MemoryCore config must be a dict: {path}")

        return config
    def _init_memory_sys(self, memory_core_config: Dict[str, Any], config_path: Path) -> MemoryCore:
        """初始化 MemoryCore 系统实例。"""
        config = memory_core_config
        workflow_id = str(config.get("workflow_id", "testrt"))
        llm_temperature = float(config.get("memory_llm_temperature", 0.0))
        similarity_threshold = float(config.get("memory_similarity_threshold", 0.88))
        top_k = int(config.get("memory_top_k", 3))
        use_threshold = bool(config.get("memory_use_threshold", True))
        scene_prompt_version = str(config.get("scene_prompt_version", "v2"))
        fact_prompt_version = str(config.get("fact_prompt_version", "v2"))
        memory_owner_name = str(config.get("memory_owner_name", "changshengEVA"))
        prompt_language = normalize_prompt_language(config.get("prompt_language", "zh"))
        runtime_prompt_config_path = resolve_related_config_path(
            config_path,
            config.get("runtime_prompt_config_path"),
            default_path=MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH,
        )

        embed_provider = str(
            config.get("embed_provider", os.getenv("EMBED_PROVIDER", "local"))
        ).strip().lower()
        if embed_provider in {"alibaba", "aliyun", "dashscope"}:
            logger.info("Embedding provider: %s (Alibaba API)", embed_provider)
            embed_func = get_alibaba_embed_model()
        elif embed_provider in {"local", "bge"}:
            logger.info("Embedding provider: %s (local BGE)", embed_provider)
            embed_func = get_local_embed_model()
        else:
            raise ValueError(
                f"Unsupported embed_provider: {embed_provider}. "
                "Use one of: local, bge, alibaba, aliyun, dashscope."
            )

        return MemoryCore(
            workflow_id=workflow_id,
            llm_func=get_llm(llm_temperature),
            embed_func=embed_func,
            llm_temperature=llm_temperature,
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            use_threshold=use_threshold,
            scene_prompt_version=scene_prompt_version,
            fact_prompt_version=fact_prompt_version,
            memory_owner_name=memory_owner_name,
            prompt_language=prompt_language,
            runtime_prompt_config_path=runtime_prompt_config_path,
        )
    @staticmethod
    def _load_facts_situation(memory_core: MemoryCore) -> Dict[str, Any]:
        """读取 facts_situation 缓存数据。"""
        facts_situation_path = getattr(memory_core, "facts_situation_file", None)
        if not facts_situation_path:
            return {}
        facts_situation_file = Path(facts_situation_path)
        if not facts_situation_file.exists():
            return {}
        try:
            with open(facts_situation_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            logger.warning("Failed to load facts_situation for bootstrap repair (%s): %s", facts_situation_file, exc)
            return {}
        return payload if isinstance(payload, dict) else {}
    @staticmethod
    def _should_repair_fact_entity_import(memory_core: MemoryCore) -> Tuple[bool, str]:
        """判断是否需要修复 fact-entity 导入。"""
        kg_base = getattr(memory_core, "kg_base", None)
        kg_store = getattr(kg_base, "store", None)
        if kg_base is None or not bool(getattr(kg_store, "available", False)):
            return False, "kg unavailable"

        facts_root_path = getattr(memory_core, "facts_dir", None)
        if not facts_root_path:
            return False, "facts dir unavailable"
        facts_root = Path(facts_root_path)
        fact_files = [p for p in facts_root.glob("*.json") if p.is_file()] if facts_root.exists() else []
        if not fact_files:
            return False, "no fact files"

        facts_situation = MemoryAgentConfigMixin._load_facts_situation(memory_core)
        expected_uids = set()
        fact_nodes = facts_situation.get("facts", {}) if isinstance(facts_situation.get("facts"), dict) else {}
        if isinstance(fact_nodes, dict):
            pending_facts = 0
            for node in fact_nodes.values():
                if not isinstance(node, dict):
                    continue
                entity_uid = str(node.get("entity_UID", "") or "").strip()
                if not entity_uid:
                    continue
                expected_uids.add(entity_uid)
                if not bool(node.get("kg_imported", False)):
                    pending_facts += 1
            if pending_facts > 0:
                return True, f"{pending_facts} fact(s) still marked kg_imported=false"

        entities = facts_situation.get("entities", []) if isinstance(facts_situation.get("entities"), list) else []
        for item in entities:
            if not isinstance(item, dict):
                continue
            entity_uid = str(item.get("UID", "") or "").strip()
            if entity_uid:
                expected_uids.add(entity_uid)

        actual_uids = set()
        try:
            actual_uids = set(str(x or "").strip() for x in kg_base.list_entity_ids())
            actual_uids.discard("")
        except Exception as exc:
            logger.warning("Failed to inspect KG entity ids for bootstrap repair: %s", exc)

        missing_uids = expected_uids - actual_uids
        if missing_uids:
            return True, f"{len(missing_uids)} expected fact entity uid(s) missing in KG"

        if not expected_uids and not actual_uids and fact_files:
            return True, "KG is empty while fact files already exist"

        return False, "KG fact entities already aligned"
    @staticmethod
    def _ensure_kg_data_initialized(memory_core: MemoryCore) -> None:
        """确保 KG 数据已完成初始化或修复。"""
        scene_files = [p for p in memory_core.scene_dir.glob("*.json") if p.is_file()]
        if scene_files:
            should_repair, reason = MemoryAgentConfigMixin._should_repair_fact_entity_import(memory_core)
            if not should_repair:
                logger.info("scene already has %d file(s), skip bootstrap import.", len(scene_files))
                return

            logger.info(
                "scene already has %d file(s); run fact-entity import repair because %s.",
                len(scene_files),
                reason,
            )
            import_result = memory_core.import_fact_entities(force_update=False, use_tqdm=True)
            if not import_result.get("success", False):
                raise RuntimeError(f"Failed to repair fact entity import: {import_result}")
            logger.info(
                "Fact entity import repair completed: scanned=%s created=%s updated=%s failed=%s",
                import_result.get("facts_scanned", 0),
                import_result.get("kg_entities_created", 0),
                import_result.get("kg_entities_updated", 0),
                import_result.get("kg_entities_failed", 0),
            )
            return

        episodes_path = memory_core.episodes_dir
        logger.info("scene is empty, bootstrap import from episodes: %s", episodes_path)
        load_result = memory_core.load_from_episode_path(episodes_path)
        if not load_result.get("success", False):
            error_text = str(load_result.get("error", ""))
            if "no episode json files found" in error_text or "path not found" in error_text:
                logger.warning(
                    "No episode data found for bootstrap, continue with empty memory state (0 entities/0 relations)."
                )
                return
            raise RuntimeError(f"Failed to initialize from episodes: {load_result}")
        logger.info(
            "Bootstrap import completed: processed=%s, failed=%s",
            load_result.get("files_processed", 0),
            load_result.get("files_failed", 0),
        )

