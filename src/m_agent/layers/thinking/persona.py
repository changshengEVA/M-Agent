"""System-prompt assembly for the thinking layer.

The thinking layer owns the assistant's persona; the execution layer is
intentionally persona-less. This module assembles the system prompt fed to
the planning / summarizing LLM calls from:

* A base ``system_prompt`` (the high-level role description).
* A ``persona_prompt`` (warm, restrained, etc.).
* The execution layer's capability boundary block (so the planning pass
  knows what NL instructions are reasonable to issue).
* An optional working-memory block rendered by :class:`WMReader`.
* An optional hidden runtime-context block (for ``source="schedule"`` etc.).
"""
from __future__ import annotations

from typing import Optional

from m_agent.prompt_utils import render_prompt_template


def merge_system_with_persona(
    base_prompt: str,
    persona_prompt: str,
    *,
    merge_template: str,
) -> str:
    """Apply the ``<base_prompt>`` / ``<persona_prompt>`` placeholders.

    Falls back to plain concatenation if the merge template is empty.
    """
    base = str(base_prompt or "").strip()
    persona = str(persona_prompt or "").strip()
    if not persona:
        return base
    template = str(merge_template or "").strip()
    if not template:
        if not base:
            return persona
        return f"{base}\n\n{persona}".strip()
    return render_prompt_template(
        template,
        {
            "<base_prompt>": base,
            "<persona_prompt>": persona,
        },
    ).strip()


_DEFAULT_RUNTIME_CONTEXT_SCHEDULE = {
    "zh": (
        "[隐藏运行时上下文]\n"
        "source=<source>\n"
        "当前这轮输入来自系统触发，而不是用户刚刚发送的新消息。\n"
        "请把可见消息当作当前需要执行的提醒/动作，并结合下面的上下文完成回复。"
        "不要误称这是用户刚刚主动发来的新消息。\n"
        "context_json=<context_json>"
    ),
    "en": (
        "[Hidden Runtime Context]\n"
        "source=<source>\n"
        "This turn was triggered by the scheduling system, not by a new live user message.\n"
        "Treat the visible message as the instruction to execute now, and use the context below."
        " Do not imply the user just sent a fresh message unless that is explicitly true.\n"
        "context_json=<context_json>"
    ),
}

_DEFAULT_RUNTIME_CONTEXT_GENERIC = {
    "zh": (
        "[隐藏运行时上下文]\n"
        "source=<source>\n"
        "当前这轮输入带有系统附加上下文。\n"
        "请把可见消息当作当前需要执行的提醒/动作，并结合下面的上下文完成回复。"
        "不要误称这是用户刚刚主动发来的新消息。\n"
        "context_json=<context_json>"
    ),
    "en": (
        "[Hidden Runtime Context]\n"
        "source=<source>\n"
        "This turn includes hidden runtime context.\n"
        "Treat the visible message as the instruction to execute now, and use the context below."
        " Do not imply the user just sent a fresh message unless that is explicitly true.\n"
        "context_json=<context_json>"
    ),
}

_DEFAULT_RUNTIME_CONTEXT = {
    "zh": (
        "[隐藏运行时上下文]\n"
        "source=<source>\n"
        "runtime_profile=langgraph_v1\n"
        "当前回合可能来自用户消息、日程触发、执行反馈或其他系统刺激。\n"
        "请根据 source 和 context_json 判断信息来源；除非 source/context 明确表示，"
        "否则不要暗示用户刚刚主动发送了新消息。\n"
        "context_json=<context_json>"
    ),
    "en": (
        "[Hidden Runtime Context]\n"
        "source=<source>\n"
        "runtime_profile=langgraph_v1\n"
        "This turn may come from a user message, schedule heartbeat, execution feedback, "
        "or another system stimulus.\n"
        "Use source and context_json to understand where the information came from; do not imply "
        "the user just sent a fresh message unless source/context explicitly say so.\n"
        "context_json=<context_json>"
    ),
}

_DEFAULT_CAPABILITY_BOUNDARY_HEADER = {
    "zh": (
        "[可委托能力]\n"
        "下列能力可以通过向执行层发送一条自然语言指令来调用；你本身没有这些工具，"
        "只能描述要做的事情。如果指令需要这些能力之外的动作，请直接回答用户。"
    ),
    "en": (
        "[Delegable Capabilities]\n"
        "You can invoke the following capabilities only by issuing a natural-language instruction to the"
        " execution layer; you do not call these tools yourself. If the user request needs anything outside"
        " these capabilities, answer the user directly without execution."
    ),
}


def _lang_key(language: str) -> str:
    return "en" if str(language or "zh").strip().lower().startswith("en") else "zh"


def build_runtime_context_block(
    *,
    source: str,
    system_context: Optional[dict],
    language: str = "zh",
    template: str = "",
    schedule_template: str = "",
    generic_template: str = "",
) -> str:
    """Render the hidden runtime-context block when ``source != 'user'`` or context provided.

    ``template`` is the unified runtime override string from YAML and supports
    ``<source>`` / ``<context_json>``. ``schedule_template`` / ``generic_template``
    are legacy override strings accepted for older runtime prompt files.
    """
    import json

    safe_source = str(source or "user").strip().lower() or "user"
    safe_context = dict(system_context or {})
    if safe_source == "user" and not safe_context:
        return ""

    context_json = json.dumps(safe_context, ensure_ascii=False, sort_keys=True) if safe_context else "{}"
    lang = _lang_key(language)
    template = str(template or "").strip()
    if not template and (schedule_template or generic_template):
        if safe_source == "schedule":
            template = str(schedule_template or "").strip() or _DEFAULT_RUNTIME_CONTEXT_SCHEDULE[lang]
        else:
            template = str(generic_template or "").strip() or _DEFAULT_RUNTIME_CONTEXT_GENERIC[lang]
    if not template:
        template = _DEFAULT_RUNTIME_CONTEXT[lang]

    return template.replace("<source>", safe_source).replace("<context_json>", context_json).strip()


def build_capability_boundary_block(
    capability_block: str,
    *,
    language: str = "zh",
    header_template: str = "",
) -> str:
    """Wrap the execution-layer capability description in a directive header.

    ``header_template`` is the override string from YAML (typically the
    ``thinking.capability_boundary_header`` field). When empty the
    built-in default for ``language`` is used. The header should NOT include
    the trailing capability list itself — that is appended by this function.
    """
    body = str(capability_block or "").strip()
    if not body:
        return ""
    lang = _lang_key(language)
    header = str(header_template or "").strip() or _DEFAULT_CAPABILITY_BOUNDARY_HEADER[lang]
    return f"{header}\n{body}".strip()
