from __future__ import annotations

import argparse
import json
import logging
import threading
import traceback
import webbrowser
from copy import deepcopy
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from m_agent.chat.simple_chat_agent import (
    DEFAULT_CHAT_CONFIG_PATH,
    create_simple_memory_chat_agent,
)
from m_agent.paths import PROJECT_ROOT
from m_agent.utils.logging_trace import FunctionTraceHandler, TraceEvent


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TRACE_LOGGER_NAMES = ("m_agent.agents.memory_agent", "Agents.memory_agent")
_AGENT_CACHE: dict[str, Any] = {}
_AGENT_CACHE_LOCK = threading.Lock()


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Test Agent Chat</title>
  <style>
    body{margin:0;background:#f3efe7;color:#211b12;font:14px/1.6 "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
    .wrap{width:min(980px,calc(100vw - 24px));margin:0 auto;padding:18px 0 24px}
    .card{background:#fffaf2;border:1px solid #dccfb9;border-radius:18px;box-shadow:0 18px 40px rgba(63,46,21,.10);padding:16px;margin-bottom:12px}
    h1{margin:0 0 8px;font-size:28px}
    p{margin:0;color:#6e6454}
    .grid{display:grid;grid-template-columns:1fr 220px auto auto;gap:10px;align-items:end}
    .meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:12px}
    label{display:block;font-size:12px;color:#6e6454;margin-bottom:6px}
    input,textarea,button{font:inherit}
    input[type=text],textarea{width:100%;padding:10px 12px;border:1px solid #dccfb9;border-radius:14px;background:#fff}
    button{border:0;border-radius:14px;padding:10px 14px;cursor:pointer}
    .primary{background:#0d6673;color:#fff}.secondary{background:#fff2dd;border:1px solid #dccfb9;color:#211b12}
    .metric{padding:12px 14px;border:1px solid #dccfb9;border-radius:14px;background:#fff}
    .metric .k{font-size:11px;color:#6e6454;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
    .metric .v{font-size:20px;font-weight:700}
    .status-running{color:#b97818}.status-done{color:#23724f}.status-failed{color:#b13f31}
    .chat{max-height:calc(100vh - 340px);min-height:56vh;overflow:auto;padding-right:4px}
    .empty{padding:18px;border:1px dashed #dccfb9;border-radius:16px;background:#fff;color:#6e6454}
    .msg{display:flex;margin-bottom:18px}.msg.user{justify-content:flex-end}
    .bubble{width:min(88%,760px);border:1px solid #dccfb9;border-radius:22px;background:#fff;overflow:hidden}
    .msg.user .bubble{background:#eef4ff;border-color:#cbd8ff}
    .head{display:flex;justify-content:space-between;gap:10px;padding:12px 15px 0;font-size:12px;color:#6e6454}
    .role{font-weight:700;color:#211b12}.body{padding:10px 15px 14px}.text{white-space:pre-wrap;word-break:break-word;font-size:15px}
    .chips{display:flex;flex-wrap:wrap;gap:8px;padding:0 15px 14px}.chip{padding:5px 10px;border:1px solid #dccfb9;border-radius:999px;background:#fff6e8;font-size:12px;color:#6e6454}
    .chip.success{color:#23724f}.chip.failed{color:#b13f31}.chip.running{color:#b97818}
    details{margin:0 15px 14px;border:1px solid #cbe0e3;border-radius:16px;background:#f7fcfc}
    summary{cursor:pointer;padding:11px 14px;font-weight:700;color:#0d6673}
    .thinking{padding:0 14px 14px;display:grid;gap:10px}
    .section{border:1px solid #dce8ea;border-radius:12px;background:#fff;padding:10px 11px}
    .section h4{margin:0 0 8px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.08em}
    .mini{white-space:pre-wrap;word-break:break-word;color:#6e6454}
    .list{display:grid;gap:8px}.item{border:1px solid #dccfb9;border-radius:12px;background:#fffdfa;padding:9px 10px}
    .item-top{display:flex;justify-content:space-between;gap:10px;margin-bottom:6px;font-size:12px;color:#6e6454}
    .item-title{font-weight:700;color:#211b12}.badge{padding:2px 8px;border:1px solid #dccfb9;border-radius:999px;background:#fff3df;font-size:11px;color:#6e6454;text-transform:uppercase}
    .badge.completed,.badge.success{color:#23724f}.badge.failed,.badge.error{color:#b13f31}.badge.running,.badge.in_progress{color:#b97818}
    .json{margin-top:8px;padding:10px;border-radius:10px;background:#f8fafc;white-space:pre-wrap;word-break:break-word;font:12px/1.6 Consolas,monospace;color:#334155}
    textarea{min-height:108px;resize:vertical}.hint{margin-top:8px;font-size:12px;color:#6e6454}.loader{color:#b97818;font-weight:700}
    @media (max-width:900px){.grid,.meta{grid-template-columns:1fr}.bubble{width:100%}.chat{max-height:none;min-height:50vh}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="card"><h1>Test Agent Chat</h1><p>每轮回答后都会把用户消息和 Agent 回复写入 <code>data\memory\test_agent</code> 并立刻导入 memory。Assistant 气泡里的 <code>Thinking</code> 会展示 MemoryCore 工具调用过程。</p></section>
    <section class="card">
      <div class="grid">
        <div><label for="configInput">Config</label><input id="configInput" type="text" value="config\prompt\test_agent_chat.yaml" /></div>
        <div><label for="threadInput">Thread</label><input id="threadInput" type="text" value="test-agent-1" /></div>
        <button id="sendBtn" class="primary">Send</button>
        <button id="resetBtn" class="secondary">Reset</button>
      </div>
      <div class="meta">
        <div class="metric"><div class="k">Status</div><div id="statusValue" class="v">Ready</div></div>
        <div class="metric"><div class="k">Messages</div><div id="messageCount" class="v">0</div></div>
        <div class="metric"><div class="k">Assistant Turns</div><div id="assistantCount" class="v">0</div></div>
        <div class="metric"><div class="k">Memory Writes</div><div id="memoryCount" class="v">0</div></div>
      </div>
    </section>
    <section class="card"><div id="chatScroll" class="chat"><div class="empty">还没有消息。你可以先聊几句，再追问“我刚才说过什么”。</div></div></section>
    <section class="card">
      <label for="messageInput">Message</label>
      <textarea id="messageInput" placeholder="输入你的消息。Enter 发送，Shift + Enter 换行。"></textarea>
      <div class="hint">建议连续使用同一个 Thread，这样更容易看到新的聊天记忆在后续轮次里被检索。</div>
    </section>
  </div>
  <script>
    function esc(v){return String(v??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;")}
    function nl(v){return esc(v).replace(/\n/g,"<br />")}
    function pretty(v){if(v===null||v===undefined||v==="")return"";if(typeof v==="string")return v;try{return JSON.stringify(v,null,2)}catch(e){return String(v)}}
    function cls(s){s=String(s||"").toLowerCase();if(["done","completed","success"].includes(s))return"success";if(["failed","error"].includes(s))return"failed";if(["running","in_progress"].includes(s))return"running";return""}
    function renderJson(v){const p=pretty(v);return p?`<div class="json">${esc(p)}</div>`:""}
    function renderPlan(plan,strategy){if(!plan&&!strategy)return"";const lines=[];if(strategy&&strategy.reason)lines.push(`Strategy: ${strategy.reason}`);if(plan&&plan.goal)lines.push(`Goal: ${plan.goal}`);if(plan&&plan.question_type)lines.push(`Type: ${plan.question_type}`);if(plan&&plan.decomposition_reason)lines.push(`Plan: ${plan.decomposition_reason}`);return `<div class="section"><h4>Plan</h4><div class="mini">${nl(lines.join("\n"))}</div></div>`}
    function renderList(title,items,mode){if(!Array.isArray(items)||!items.length)return"";return `<div class="section"><h4>${title}</h4><div class="list">${items.map((item)=>{if(mode==="subq"){return `<div class="item"><div class="item-top"><span class="badge ${esc(item.status||"pending")}">${esc(item.status||"pending")}</span><span>#${esc(item.index??"?")}</span></div><div class="item-title">${esc(item.question||"")}</div>${item.answer?`<div class="mini" style="margin-top:8px;">${nl(item.answer)}</div>`:""}${item.error?`<div class="mini" style="margin-top:8px;color:#b13f31;">${nl(item.error)}</div>`:""}</div>`}if(mode==="tool"){return `<div class="item"><div class="item-top"><span class="item-title">#${esc(item.call_id??"?")} ${esc(item.tool_name||"tool")}</span><span class="badge ${esc(item.status||"unknown")}">${esc(item.status||"unknown")}</span></div>${item.error?`<div class="mini" style="color:#b13f31;">${nl(item.error)}</div>`:""}${item.params?`<div class="mini" style="margin-top:8px;">Params</div>${renderJson(item.params)}`:""}${item.result?`<div class="mini" style="margin-top:8px;">Result</div>${renderJson(item.result)}`:""}</div>`}return `<div class="item"><div class="item-top"><span class="badge">${esc(item.kind||"event")}</span><span>${esc(item.timestamp||"")}</span></div><div class="item-title">${esc(item.title||"")}</div><div class="mini" style="margin-top:7px;">${nl(item.summary||"")}</div></div>`}).join("")}</div></div>`}
    function renderMemoryWrite(item){if(!item)return"";const lines=[];if(item.workflow_id)lines.push(`workflow: ${item.workflow_id}`);if(item.dialogue_id)lines.push(`dialogue: ${item.dialogue_id}`);if(item.dialogue_file)lines.push(`dialogue_file: ${item.dialogue_file}`);if(item.episode_file)lines.push(`episode_file: ${item.episode_file}`);if(item.error)lines.push(`error: ${item.error}`);return `<div class="section"><h4>Memory Write</h4><div class="mini">${nl(lines.join("\n"))}</div></div>`}
    function renderThinking(msg){if(msg.role!=="assistant")return"";const trace=msg.trace||{};const plan=Object.keys(trace.live_plan||{}).length?trace.live_plan:((msg.agent_result||{}).question_plan||null);const subqs=Array.isArray(trace.live_sub_questions)&&trace.live_sub_questions.length?trace.live_sub_questions:(((msg.agent_result||{}).sub_question_results)||[]);const tools=Array.isArray(trace.tool_steps)&&trace.tool_steps.length?trace.tool_steps:(((msg.agent_result||{}).tool_calls)||[]);const events=Array.isArray(trace.events)?trace.events:[];if(!plan&&!subqs.length&&!tools.length&&!events.length&&!msg.memory_write&&msg.status!=="running")return"";const label=msg.status==="running"?`Thinking...${tools.length?` 已记录 ${tools.length} 次工具调用`:""}`:`Thinking · ${tools.length} tool call(s)`;return `<details data-message-id="${esc(msg.id)}" ${msg.status==="running"?"open":""}><summary>${esc(label)}</summary><div class="thinking">${renderPlan(plan,trace.question_strategy||null)}${renderList("Sub-Questions",subqs,"subq")}${renderList("Memory Tools",tools,"tool")}${renderList("Timeline",events.slice(-10),"event")}${renderMemoryWrite(msg.memory_write)}</div></details>`}
    function renderChips(msg){if(msg.role!=="assistant")return"";const out=[];if(msg.status)out.push(`<span class="chip ${cls(msg.status)}">${esc(msg.status)}</span>`);const tools=Array.isArray(((msg.agent_result||{}).tool_calls))?((msg.agent_result||{}).tool_calls).length:0;if(tools)out.push(`<span class="chip">tools: ${tools}</span>`);if(msg.memory_write&&msg.memory_write.workflow_id)out.push(`<span class="chip ${msg.memory_write.success?"success":"failed"}">memory: ${esc(msg.memory_write.workflow_id)}</span>`);return out.join("")}
    function renderMsg(msg){const body=msg.role==="assistant"?(msg.status==="running"&&!msg.content?`<div class="loader">正在检索记忆并生成回答...</div>`:`<div class="text">${msg.status==="failed"?nl(msg.error_text||"运行失败"):nl(msg.content||"")}</div>`):`<div class="text">${nl(msg.content||"")}</div>`;return `<div class="msg ${esc(msg.role)}"><div class="bubble"><div class="head"><div class="role">${msg.role==="user"?"User":"Assistant"}</div><div>${esc(msg.timestamp||"")}</div></div><div class="body">${body}</div>${renderThinking(msg)}${renderChips(msg)?`<div class="chips">${renderChips(msg)}</div>`:""}</div></div>`}
    function getOpenThinkingIds(root){return new Set(Array.from(root.querySelectorAll("details[data-message-id][open]")).map((node)=>String(node.getAttribute("data-message-id")||"")).filter(Boolean))}
    function restoreOpenThinkingIds(root,openIds){if(!(openIds instanceof Set))return;root.querySelectorAll("details[data-message-id]").forEach((node)=>{const id=String(node.getAttribute("data-message-id")||"");if(openIds.has(id))node.open=true})}
    function render(snapshot){const chat=document.getElementById("chatScroll");const prev=chat.scrollHeight-chat.scrollTop-chat.clientHeight;const stick=prev<120;const openIds=getOpenThinkingIds(chat);const messages=Array.isArray(snapshot&&snapshot.messages)?snapshot.messages:[];const assistants=messages.filter((m)=>m.role==="assistant");const writes=assistants.filter((m)=>m.memory_write&&m.memory_write.success);const status=String((snapshot&&snapshot.status)||"Ready");const statusNode=document.getElementById("statusValue");statusNode.textContent=status;statusNode.className=`v status-${status.toLowerCase()}`;document.getElementById("messageCount").textContent=String(messages.length);document.getElementById("assistantCount").textContent=String(assistants.length);document.getElementById("memoryCount").textContent=String(writes.length);chat.innerHTML=messages.length?messages.map(renderMsg).join(""):`<div class="empty">还没有消息。你可以先聊几句，再追问“我刚才说过什么”。</div>`;restoreOpenThinkingIds(chat,openIds);if(stick)chat.scrollTop=chat.scrollHeight}
    async function postJson(url,payload){const res=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload||{})});const data=await res.json().catch(()=>({}));if(!res.ok)throw new Error(data.error||`Request failed: ${res.status}`);return data}
    async function sendMessage(){const input=document.getElementById("messageInput");const question=String(input.value||"").trim();if(!question)return;await postJson("/api/chat",{config:document.getElementById("configInput").value,thread_id:document.getElementById("threadInput").value,question}).catch((e)=>alert(e.message||String(e)));input.value="";input.focus()}
    async function resetView(){await postJson("/api/reset",{}).catch((e)=>alert(e.message||String(e)))}
    async function poll(){try{const res=await fetch("/api/state",{cache:"no-store"});render(await res.json())}catch(e){console.error(e)}finally{setTimeout(poll,1000)}}
    document.getElementById("sendBtn").addEventListener("click",sendMessage)
    document.getElementById("resetBtn").addEventListener("click",resetView)
    document.getElementById("messageInput").addEventListener("keydown",(event)=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();sendMessage()}})
    poll()
  </script>
</body>
</html>
"""


def _resolve_config_path(config_text: str) -> Path:
    config_path = Path(config_text or str(DEFAULT_CHAT_CONFIG_PATH))
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    return config_path.resolve()


def _get_cached_chat_agent(config_text: str):
    config_path = _resolve_config_path(config_text)
    cache_key = str(config_path)
    with _AGENT_CACHE_LOCK:
        agent = _AGENT_CACHE.get(cache_key)
        if agent is None:
            logger.info("Create chat agent for config=%s", config_path)
            agent = create_simple_memory_chat_agent(config_path=config_path)
            _AGENT_CACHE[cache_key] = agent
        return agent


class ChatRunState:
    PLAN_UPDATE_PREFIX = "PLAN UPDATE: "
    SUBQ_START_PREFIX = "SUBQ START: "
    SUBQ_DONE_PREFIX = "SUBQ DONE: "
    QUESTION_STRATEGY_PREFIX = "QUESTION STRATEGY: "
    DIRECT_ANSWER_PREFIX = "DIRECT ANSWER PAYLOAD: "
    DIRECT_FALLBACK_PREFIX = "DIRECT ANSWER FALLBACK: "
    TOOL_CALL_PREFIX = "TOOL CALL DETAIL: "
    TOOL_RESULT_PREFIX = "TOOL RESULT DETAIL: "
    FINAL_PAYLOAD_PREFIX = "FINAL ANSWER PAYLOAD: "

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._message_seq = 0
        self._event_seq = 0
        self._reset_locked()

    def _reset_locked(self) -> None:
        self.running = False
        self.status = "Ready"
        self.config = str(DEFAULT_CHAT_CONFIG_PATH)
        self.thread_id = "test-agent-1"
        self.messages: list[dict[str, Any]] = []
        self.current_assistant_id: Optional[int] = None

    def reset(self) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("A run is still in progress.")
            self._message_seq = 0
            self._event_seq = 0
            self._reset_locked()

    def begin_run(self, *, config: str, thread_id: str, question: str) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("A run is already in progress.")
            self.running = True
            self.status = "Running"
            self.config = config
            self.thread_id = thread_id
            self.messages.append(self._make_message("user", question, question, "done"))
            assistant = self._make_message("assistant", "", question, "running")
            self.messages.append(assistant)
            self.current_assistant_id = int(assistant["id"])

    def append_trace(self, event: TraceEvent) -> None:
        with self._lock:
            assistant = self._current_assistant_locked()
            if assistant is None:
                return
            item = self._build_event_item(event)
            assistant["trace"]["events"].append(item)
            self._apply_event(assistant, item)

    def set_result(self, result: Dict[str, Any]) -> None:
        with self._lock:
            assistant = self._current_assistant_locked()
            if assistant is None:
                return
            assistant["status"] = "done"
            assistant["content"] = str(result.get("answer", "") or "")
            assistant["agent_result"] = deepcopy(result.get("agent_result"))
            assistant["memory_write"] = deepcopy(result.get("memory_write"))
            trace = assistant["trace"]
            agent_result = result.get("agent_result") if isinstance(result.get("agent_result"), dict) else {}
            if isinstance(agent_result.get("question_plan"), dict) and not trace["live_plan"]:
                trace["live_plan"] = deepcopy(agent_result["question_plan"])
            if isinstance(agent_result.get("sub_question_results"), list) and not trace["live_sub_questions"]:
                trace["live_sub_questions"] = [
                    deepcopy(x) for x in agent_result["sub_question_results"] if isinstance(x, dict)
                ]
            if isinstance(agent_result.get("tool_calls"), list) and not trace["tool_steps"]:
                trace["tool_steps"] = [deepcopy(x) for x in agent_result["tool_calls"] if isinstance(x, dict)]
            if isinstance(agent_result, dict) and not trace["final_answer_payload"]:
                trace["final_answer_payload"] = deepcopy(agent_result)
            self.running = False
            self.status = "Done"
            self.current_assistant_id = None

    def set_error(self, error_text: str) -> None:
        with self._lock:
            assistant = self._current_assistant_locked()
            if assistant is not None:
                assistant["status"] = "failed"
                assistant["error_text"] = error_text
            self.running = False
            self.status = "Failed"
            self.current_assistant_id = None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "status": self.status,
                "config": self.config,
                "thread_id": self.thread_id,
                "messages": [self._serialize_message(x) for x in self.messages],
            }

    def _make_message(self, role: str, content: str, question: str, status: str) -> Dict[str, Any]:
        self._message_seq += 1
        return {
            "id": self._message_seq,
            "role": role,
            "content": content,
            "question": question,
            "status": status,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "error_text": None,
            "agent_result": None,
            "memory_write": None,
            "trace": {
                "events": [],
                "question_strategy": {},
                "live_plan": {},
                "live_sub_questions": [],
                "tool_steps": [],
                "direct_answer_payload": None,
                "direct_answer_fallback": None,
                "final_answer_payload": None,
                "_tool_steps_by_id": {},
            },
        }

    def _current_assistant_locked(self) -> Optional[Dict[str, Any]]:
        if self.current_assistant_id is None:
            return None
        for item in reversed(self.messages):
            if item.get("id") == self.current_assistant_id:
                return item
        return None

    def _next_event_id_locked(self) -> int:
        self._event_seq += 1
        return self._event_seq

    @staticmethod
    def _load_json_payload(raw: str, prefix: str) -> Any:
        try:
            return json.loads(raw[len(prefix) :].strip())
        except Exception:
            return raw[len(prefix) :].strip()

    def _build_event_item(self, event: TraceEvent) -> Dict[str, Any]:
        raw = event.raw_message or ""
        payload: Any = None
        kind = "log"
        title = event.function_name or event.logger_name
        summary = event.detail or raw
        if raw.startswith(self.QUESTION_STRATEGY_PREFIX):
            kind = "question_strategy"
            title = "Question Strategy"
            payload = self._load_json_payload(raw, self.QUESTION_STRATEGY_PREFIX)
            if isinstance(payload, dict):
                summary = f"decompose_first={payload.get('decompose_first')} | reason={payload.get('reason')}"
        elif raw.startswith(self.PLAN_UPDATE_PREFIX):
            kind = "plan_update"
            title = "Question Plan"
            payload = self._load_json_payload(raw, self.PLAN_UPDATE_PREFIX)
            if isinstance(payload, dict):
                subq = payload.get("sub_questions", [])
                count = len(subq) if isinstance(subq, list) else 0
                summary = f"{payload.get('question_type', 'unknown')} | {count} sub-question(s)"
        elif raw.startswith(self.SUBQ_START_PREFIX):
            kind = "subq_start"
            title = "Sub-question Start"
            payload = self._load_json_payload(raw, self.SUBQ_START_PREFIX)
            summary = self._subq_summary(payload)
        elif raw.startswith(self.SUBQ_DONE_PREFIX):
            kind = "subq_done"
            title = "Sub-question Done"
            payload = self._load_json_payload(raw, self.SUBQ_DONE_PREFIX)
            summary = self._subq_summary(payload)
        elif raw.startswith(self.TOOL_CALL_PREFIX):
            kind = "tool_call_detail"
            title = "Tool Call"
            payload = self._load_json_payload(raw, self.TOOL_CALL_PREFIX)
            summary = self._tool_summary(payload)
        elif raw.startswith(self.TOOL_RESULT_PREFIX):
            kind = "tool_result_detail"
            title = "Tool Result"
            payload = self._load_json_payload(raw, self.TOOL_RESULT_PREFIX)
            summary = self._tool_result_summary(payload)
        elif raw.startswith(self.DIRECT_ANSWER_PREFIX):
            kind = "direct_answer_payload"
            title = "Direct Answer Payload"
            payload = self._load_json_payload(raw, self.DIRECT_ANSWER_PREFIX)
        elif raw.startswith(self.DIRECT_FALLBACK_PREFIX):
            kind = "direct_answer_fallback"
            title = "Direct Answer Fallback"
            payload = self._load_json_payload(raw, self.DIRECT_FALLBACK_PREFIX)
        elif raw.startswith(self.FINAL_PAYLOAD_PREFIX):
            kind = "final_answer_payload"
            title = "Final Answer Payload"
            payload = self._load_json_payload(raw, self.FINAL_PAYLOAD_PREFIX)
        elif event.function_name:
            kind = "api_call" if (event.phase or "").upper() == "CALL" else "api_response"
            title = f"{event.function_name} {kind.replace('_', ' ').title()}"
        elif event.level_name == "ERROR":
            kind = "error"
        return {
            "id": self._next_event_id_locked(),
            "timestamp": event.timestamp,
            "kind": kind,
            "title": title,
            "summary": summary,
            "payload": payload,
        }

    @staticmethod
    def _subq_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        answer = str(payload.get("answer", "") or "").strip()
        summary = f"#{payload.get('index')} [{payload.get('status')}] {payload.get('question', '')}"
        if answer:
            summary += f" -> {answer[:120]}"
        return summary

    @staticmethod
    def _tool_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        params = payload.get("params", {})
        if isinstance(params, dict) and params:
            key = next(iter(params))
            return f"#{payload.get('call_id')} {payload.get('tool_name')}({key}={params[key]})"
        return f"#{payload.get('call_id')} {payload.get('tool_name')}"

    @staticmethod
    def _tool_result_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        return f"#{payload.get('call_id')} {payload.get('tool_name')} -> {payload.get('status')}"

    def _apply_event(self, assistant: Dict[str, Any], event_item: Dict[str, Any]) -> None:
        trace = assistant["trace"]
        kind = event_item["kind"]
        payload = event_item.get("payload")
        if kind == "question_strategy" and isinstance(payload, dict):
            trace["question_strategy"] = deepcopy(payload)
        elif kind == "plan_update" and isinstance(payload, dict):
            trace["live_plan"] = deepcopy(payload)
            self._init_subqs(trace)
        elif kind == "subq_start":
            self._update_subq(trace, payload, "in_progress")
        elif kind == "subq_done":
            self._update_subq(trace, payload, "completed")
        elif kind == "tool_call_detail" and isinstance(payload, dict):
            call_id = self._tool_call_id(payload)
            if call_id is not None:
                trace["_tool_steps_by_id"][call_id] = deepcopy(payload)
                trace["tool_steps"] = self._tool_steps(trace)
        elif kind == "tool_result_detail" and isinstance(payload, dict):
            call_id = self._tool_call_id(payload)
            if call_id is not None:
                current = trace["_tool_steps_by_id"].get(call_id, {})
                current.update(deepcopy(payload))
                trace["_tool_steps_by_id"][call_id] = current
                trace["tool_steps"] = self._tool_steps(trace)
        elif kind == "direct_answer_payload" and isinstance(payload, dict):
            trace["direct_answer_payload"] = deepcopy(payload)
        elif kind == "direct_answer_fallback":
            trace["direct_answer_fallback"] = deepcopy(payload)
        elif kind == "final_answer_payload" and isinstance(payload, dict):
            trace["final_answer_payload"] = deepcopy(payload)

    @staticmethod
    def _tool_call_id(payload: Dict[str, Any]) -> Optional[int]:
        try:
            return int(payload.get("call_id"))
        except Exception:
            return None

    @staticmethod
    def _init_subqs(trace: Dict[str, Any]) -> None:
        trace["live_sub_questions"] = []
        items = trace["live_plan"].get("sub_questions", [])
        if not isinstance(items, list):
            return
        for idx, item in enumerate(items, start=1):
            text = str(item).strip()
            if text:
                trace["live_sub_questions"].append(
                    {"index": idx, "question": text, "status": "pending", "answer": ""}
                )

    @staticmethod
    def _update_subq(trace: Dict[str, Any], payload: Any, default_status: str) -> None:
        if not isinstance(payload, dict):
            return
        try:
            index_int = int(payload.get("index"))
        except Exception:
            return
        while len(trace["live_sub_questions"]) < index_int:
            trace["live_sub_questions"].append(
                {
                    "index": len(trace["live_sub_questions"]) + 1,
                    "question": "",
                    "status": "pending",
                    "answer": "",
                }
            )
        item = trace["live_sub_questions"][index_int - 1]
        if payload.get("question") is not None:
            item["question"] = str(payload.get("question") or "")
        item["index"] = index_int
        item["status"] = str(payload.get("status") or default_status)
        if payload.get("answer") is not None:
            item["answer"] = str(payload.get("answer") or "")
        if payload.get("error") is not None:
            item["error"] = str(payload.get("error") or "")

    @staticmethod
    def _tool_steps(trace: Dict[str, Any]) -> list[Dict[str, Any]]:
        return [deepcopy(trace["_tool_steps_by_id"][key]) for key in sorted(trace["_tool_steps_by_id"])]

    @staticmethod
    def _serialize_message(message: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(message)
        item["trace"].pop("_tool_steps_by_id", None)
        return item


def run_chat_once(state: ChatRunState, *, config_text: str, question: str, thread_id: str) -> None:
    handler = FunctionTraceHandler(callback=state.append_trace, include_non_api=True)
    trace_loggers = []
    for logger_name in TRACE_LOGGER_NAMES:
        trace_logger = logging.getLogger(logger_name)
        trace_logger.addHandler(handler)
        trace_logger.setLevel(logging.INFO)
        trace_loggers.append(trace_logger)
    try:
        agent = _get_cached_chat_agent(config_text)
        state.set_result(agent.chat(message=question, thread_id=thread_id or None))
    except Exception:
        state.set_error(traceback.format_exc())
    finally:
        for trace_logger in trace_loggers:
            trace_logger.removeHandler(handler)


def create_handler(state: ChatRunState):
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "TestAgentChatHTTP/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            logger.info("%s - %s", self.address_string(), format % args)

        def _read_json_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                data = {}
            return data if isinstance(data, dict) else {}

        def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_html(self, html_text: str) -> None:
            encoded = html_text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send_html(HTML_PAGE)
                return
            if path == "/api/state":
                self._send_json(state.snapshot())
                return
            self._send_json({"error": "Not found"}, status=404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/chat":
                payload = self._read_json_body()
                config_text = str(payload.get("config", str(DEFAULT_CHAT_CONFIG_PATH)) or "").strip()
                thread_id = str(payload.get("thread_id", "test-agent-1") or "").strip()
                question = str(payload.get("question", "") or "").strip()
                if not question:
                    self._send_json({"error": "Question is empty."}, status=400)
                    return
                try:
                    state.begin_run(config=config_text, thread_id=thread_id, question=question)
                except RuntimeError as exc:
                    self._send_json({"error": str(exc)}, status=409)
                    return
                worker = threading.Thread(
                    target=run_chat_once,
                    kwargs={
                        "state": state,
                        "config_text": config_text,
                        "question": question,
                        "thread_id": thread_id,
                    },
                    daemon=True,
                )
                worker.start()
                self._send_json({"ok": True})
                return

            if path == "/api/reset":
                try:
                    state.reset()
                except RuntimeError as exc:
                    self._send_json({"error": str(exc)}, status=409)
                    return
                self._send_json({"ok": True})
                return

            self._send_json({"error": "Not found"}, status=404)

    return RequestHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the test_agent chat web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8766, help="Bind port. Default: 8766")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open the browser.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = ChatRunState()
    handler_cls = create_handler(state)
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    url = f"http://{args.host}:{args.port}/"
    logger.info("Test Agent Chat UI listening on %s", url)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("Failed to auto-open browser. Open %s manually.", url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down chat UI...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
