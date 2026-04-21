from __future__ import annotations


def test_init_memory_sys_uses_llm_model_name(monkeypatch) -> None:
    """MemoryCore LLM should honor llm_model_name from core config.

    We do not test network calls; only that the model factory receives the configured id.
    """

    from m_agent.agents.memory_agent.mixins.config import MemoryAgentConfigMixin

    seen = {}

    def _fake_get_chat_llm(*, model_temperature: float, model_name: str | None = None, **_kwargs):
        seen["temperature"] = model_temperature
        seen["model_name"] = model_name

        def _llm(_prompt: str) -> str:
            return "{}"

        return _llm

    monkeypatch.setattr(
        "m_agent.agents.memory_agent.mixins.config.get_chat_llm",
        _fake_get_chat_llm,
        raising=True,
    )

    # Patch MemoryCore itself so we don't touch Neo4j / services.
    class _StubMemoryCore:
        def __init__(self, **kwargs):
            self.kwargs = dict(kwargs)

    monkeypatch.setattr(
        "m_agent.agents.memory_agent.mixins.config.MemoryCore",
        _StubMemoryCore,
        raising=True,
    )

    # Also patch embed model factories.
    monkeypatch.setattr(
        "m_agent.agents.memory_agent.mixins.config.get_local_embed_model",
        lambda: (lambda _t: [0.0]),
        raising=True,
    )

    core_cfg = {
        "workflow_id": "test-workflow",
        "prompt_language": "en",
        "runtime_prompt_config_path": "./runtime/memory_core_runtime.yaml",
        "llm_provider": "openai",
        "llm_model_name": "gpt-4o-mini",
        "embed_provider": "local",
        "memory_llm_temperature": 0.0,
    }

    # Use config mixin method directly.
    mixin = MemoryAgentConfigMixin()
    mc = mixin._init_memory_sys(
        core_cfg,
        config_path=__import__("pathlib").Path("f:/AI/M-Agent/config/memory/core/dev_openai_memory_core.yaml"),
    )
    assert mc is not None
    assert seen.get("model_name") == "gpt-4o-mini"

