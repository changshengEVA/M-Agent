from __future__ import annotations

import argparse
import json
import logging
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


logger = logging.getLogger(__name__)

HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>M-Agent API Demo</title>
  <style>
    :root{
      --bg:#f3efe6;
      --panel:#fffaf1;
      --line:#d6c8ae;
      --ink:#1f180f;
      --muted:#6c6254;
      --accent:#0e5f68;
      --accent-soft:#dff0f2;
      --warn:#b47817;
      --ok:#236b46;
      --err:#b23e31;
      --shadow:0 22px 44px rgba(60,42,18,.10);
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      color:var(--ink);
      background:
        radial-gradient(circle at top right, rgba(14,95,104,.14), transparent 28%),
        radial-gradient(circle at left bottom, rgba(180,120,23,.10), transparent 24%),
        var(--bg);
      font:14px/1.6 "Avenir Next","PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif;
    }
    .wrap{width:min(1260px,calc(100vw - 28px));margin:0 auto;padding:18px 0 26px}
    .hero,.card{
      background:linear-gradient(180deg, rgba(255,255,255,.72), rgba(255,250,241,.96));
      border:1px solid var(--line);
      border-radius:22px;
      box-shadow:var(--shadow);
    }
    .hero{padding:18px 18px 14px;margin-bottom:14px}
    .title{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}
    h1{margin:0;font:700 30px/1.1 "Avenir Next","PingFang SC","Microsoft YaHei",sans-serif}
    .sub{margin-top:8px;color:var(--muted);max-width:920px}
    .pill{
      display:inline-flex;align-items:center;gap:6px;
      padding:6px 11px;border-radius:999px;border:1px solid var(--line);
      background:#fff5e5;color:var(--muted);font-size:12px
    }
    .row{display:grid;gap:14px}
    .row.top{grid-template-columns:1.15fr .85fr}
    .row.bottom{grid-template-columns:.95fr 1.05fr}
    .card{padding:14px}
    .grid{display:grid;gap:10px}
    .grid.controls{grid-template-columns:1.2fr .9fr .8fr auto}
    .grid.service{grid-template-columns:1.15fr .9fr .8fr .8fr}
    .grid.actions{grid-template-columns:repeat(5,minmax(0,1fr))}
    label{display:block;margin-bottom:6px;color:var(--muted);font-size:12px}
    input[type=text],textarea{
      width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:14px;
      background:#fff;color:var(--ink);font:inherit
    }
    textarea{min-height:112px;resize:vertical}
    input[readonly]{background:#fbf7ef;color:#534c40}
    button{
      border:0;border-radius:14px;padding:11px 14px;cursor:pointer;font:600 14px/1.2 inherit
    }
    .primary{background:var(--accent);color:#fff}
    .secondary{background:#fff3de;color:var(--ink);border:1px solid var(--line)}
    .secondary:hover,.primary:hover{filter:brightness(.98)}
    .stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-top:12px}
    .metric{padding:12px 14px;border:1px solid var(--line);border-radius:15px;background:#fff}
    .metric .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
    .metric .v{font-size:22px;font-weight:700}
    .v.idle{color:var(--muted)}
    .v.starting,.v.running{color:var(--warn)}
    .v.completed{color:var(--ok)}
    .v.failed,.v.offline{color:var(--err)}
    .section-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:10px}
    .section-title{font-weight:700;font-size:16px}
    .section-sub{color:var(--muted);font-size:12px}
    .answer{
      min-height:120px;padding:14px;border:1px solid var(--line);border-radius:18px;
      background:linear-gradient(180deg, #fff, #fff9ef);white-space:pre-wrap;word-break:break-word;font-size:15px
    }
    .muted{color:var(--muted)}
    .events{max-height:min(70vh,760px);overflow:auto;padding-right:4px}
    .event{
      border:1px solid var(--line);border-radius:16px;background:#fff;margin-bottom:10px;padding:11px 12px
    }
    .event-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:7px}
    .event-type{font-weight:700}
    .event-time{color:var(--muted);font-size:12px}
    .event pre,.raw pre{
      margin:8px 0 0;padding:10px;border-radius:12px;background:#f7fafc;
      color:#334155;overflow:auto;font:12px/1.55 Consolas,monospace
    }
    .tag-run_started,.tag-run_completed,.tag-assistant_message{color:var(--accent)}
    .tag-recall_started,.tag-question_strategy,.tag-plan_update{color:var(--warn)}
    .tag-tool_call{color:#0f766e}
    .tag-tool_result,.tag-recall_completed,.tag-memory_capture_updated,.tag-thread_state_updated{color:var(--ok)}
    .tag-run_failed,.tag-direct_answer_fallback{color:var(--err)}
    .raw{max-height:300px;overflow:auto}
    .hint{margin-top:8px;color:var(--muted);font-size:12px}
    @media (max-width:1120px){
      .row.top,.row.bottom,.grid.controls,.grid.service,.grid.actions,.stats{grid-template-columns:1fr}
      .events{max-height:none}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="title">
        <div>
          <h1>M-Agent API Demo</h1>
          <div class="sub">这个页面只通过 HTTP API 和 SSE 与后端交互。你可以在这里连续聊天、查看主控的工具调用事件、观察 thread buffer/history 状态，并手动控制 memory mode 与 flush。</div>
        </div>
        <span id="memoryPill" class="pill">memory: pending</span>
      </div>
    </section>

    <section class="card" style="margin-bottom:14px;">
      <div class="grid controls">
        <div>
          <label for="messageInput">Message</label>
          <textarea id="messageInput" placeholder="例如：我之前提过的旅行计划是什么？"></textarea>
        </div>
        <div>
          <label for="apiBaseInput">API Base URL</label>
          <input id="apiBaseInput" type="text" value="__API_BASE_URL__" />
        </div>
        <div>
          <label for="threadInput">Thread ID</label>
          <input id="threadInput" type="text" value="demo-thread-1" />
        </div>
        <button id="sendBtn" class="primary">Send</button>
      </div>

      <div class="grid service" style="margin-top:10px;">
        <div>
          <label for="configPathInput">Service Config</label>
          <input id="configPathInput" type="text" readonly value="(loading)" />
        </div>
        <div>
          <label for="defaultThreadInput">Default Thread</label>
          <input id="defaultThreadInput" type="text" readonly value="(loading)" />
        </div>
        <div>
          <label for="persistInput">Persist Memory</label>
          <input id="persistInput" type="text" readonly value="(loading)" />
        </div>
        <div>
          <label for="serviceStateInput">Service State</label>
          <input id="serviceStateInput" type="text" readonly value="checking" />
        </div>
      </div>

      <div class="grid service" style="margin-top:10px;">
        <div>
          <label for="runInput">Run ID</label>
          <input id="runInput" type="text" readonly value="" />
        </div>
        <div>
          <label for="recallInput">Recall Mode</label>
          <input id="recallInput" type="text" readonly value="-" />
        </div>
        <div>
          <label for="pendingInput">Pending Rounds</label>
          <input id="pendingInput" type="text" readonly value="0" />
        </div>
        <div>
          <label for="threadModeInput">Thread Mode</label>
          <input id="threadModeInput" type="text" readonly value="(loading)" />
        </div>
      </div>

      <div class="grid actions" style="margin-top:10px;">
        <button id="syncBtn" class="secondary">Sync Service</button>
        <button id="threadStateBtn" class="secondary">Refresh Thread</button>
        <button id="manualModeBtn" class="secondary">Mode: Manual</button>
        <button id="offModeBtn" class="secondary">Mode: Off</button>
        <button id="flushBtn" class="secondary">Flush Thread</button>
      </div>

      <div class="stats">
        <div class="metric"><div class="k">Run Status</div><div id="statusValue" class="v idle">idle</div></div>
        <div class="metric"><div class="k">Events</div><div id="eventCount" class="v">0</div></div>
        <div class="metric"><div class="k">Tool Calls</div><div id="toolCount" class="v">0</div></div>
        <div class="metric"><div class="k">History Rounds</div><div id="historyRoundsValue" class="v">0</div></div>
        <div class="metric"><div class="k">Pending Turns</div><div id="pendingTurnsValue" class="v">0</div></div>
        <div class="metric"><div class="k">Memory State</div><div id="memoryValue" class="v">-</div></div>
      </div>

      <div class="hint">服务端 config 在启动时固定。这个 demo 会主动读取服务端的 runtime 和 thread state，不会再向后端传请求级 config。</div>
    </section>

    <section class="row top">
      <section class="card">
        <div class="section-head">
          <div>
            <div class="section-title">Answer</div>
            <div class="section-sub">当前 run 的最终回复</div>
          </div>
        </div>
        <div id="answerBox" class="answer muted">等待发起 chat run。</div>
      </section>

      <section class="card">
        <div class="section-head">
          <div>
            <div class="section-title">Thread State</div>
            <div class="section-sub">当前 thread 的 history / pending buffer 快照</div>
          </div>
        </div>
        <div class="raw"><pre id="threadStateBox">{}</pre></div>
      </section>
    </section>

    <section class="row bottom" style="margin-top:14px;">
      <section class="card">
        <div class="section-head">
          <div>
            <div class="section-title">Run Snapshot</div>
            <div class="section-sub">`GET /v1/chat/runs/{run_id}` 返回结果</div>
          </div>
        </div>
        <div class="raw"><pre id="snapshotBox">{}</pre></div>
      </section>

      <section class="card">
        <div class="section-head">
          <div>
            <div class="section-title">Event Stream</div>
            <div class="section-sub">SSE 实时事件</div>
          </div>
        </div>
        <div id="eventsBox" class="events"></div>
      </section>
    </section>
  </div>

  <script>
    const knownTypes = [
      "run_started","question_strategy","plan_update","recall_started","tool_call",
      "tool_result","sub_question_started","sub_question_completed","direct_answer_payload",
      "direct_answer_fallback","final_answer_payload","recall_completed","assistant_message",
      "memory_capture_updated","thread_state_updated","chat_result","run_completed","run_failed"
    ];

    const state = {
      source: null,
      runId: "",
      resultUrl: "",
      eventsUrl: "",
      configPath: "",
      defaultThreadId: "",
      persistMemory: "",
      serviceState: "checking",
      eventCount: 0,
      toolCount: 0,
      recallMode: "-",
      memoryState: "-",
      threadState: {},
      lastSnapshot: {}
    };

    function esc(value){
      return String(value ?? "")
        .replace(/&/g,"&amp;")
        .replace(/</g,"&lt;")
        .replace(/>/g,"&gt;")
        .replace(/"/g,"&quot;")
        .replace(/'/g,"&#39;");
    }

    function pretty(value){
      if(value === null || value === undefined) return "";
      if(typeof value === "string") return value;
      try { return JSON.stringify(value, null, 2); } catch { return String(value); }
    }

    function baseUrl(){
      return String(document.getElementById("apiBaseInput").value || "").trim().replace(/\/+$/,"");
    }

    function joinUrl(path){
      if(!path) return baseUrl();
      if(/^https?:\/\//i.test(path)) return path;
      return `${baseUrl()}${path.startsWith("/") ? "" : "/"}${path}`;
    }

    function threadIdValue(){
      return String(document.getElementById("threadInput").value || "").trim() || state.defaultThreadId || "demo-thread-1";
    }

    function setStatus(status){
      const node = document.getElementById("statusValue");
      const value = String(status || "idle").toLowerCase();
      node.textContent = value;
      node.className = `v ${esc(value)}`;
    }

    function setAnswer(text, muted=false){
      const node = document.getElementById("answerBox");
      node.textContent = text || "";
      node.className = `answer ${muted ? "muted" : ""}`;
    }

    function setSnapshot(value){
      state.lastSnapshot = value || {};
      document.getElementById("snapshotBox").textContent = pretty(state.lastSnapshot);
    }

    function setMemoryPill(text){
      document.getElementById("memoryPill").textContent = text;
    }

    function setThreadState(value){
      state.threadState = value || {};
      document.getElementById("threadStateBox").textContent = pretty(state.threadState);
      updateMetrics();
    }

    function updateMetrics(){
      document.getElementById("eventCount").textContent = String(state.eventCount);
      document.getElementById("toolCount").textContent = String(state.toolCount);
      document.getElementById("runInput").value = state.runId || "";
      document.getElementById("configPathInput").value = state.configPath || "(loading)";
      document.getElementById("defaultThreadInput").value = state.defaultThreadId || "(loading)";
      document.getElementById("persistInput").value = state.persistMemory || "(loading)";
      document.getElementById("serviceStateInput").value = state.serviceState || "checking";
      document.getElementById("recallInput").value = state.recallMode || "-";
      document.getElementById("threadModeInput").value = String((state.threadState || {}).mode || "-");
      document.getElementById("pendingInput").value = String((state.threadState || {}).pending_rounds || 0);
      document.getElementById("historyRoundsValue").textContent = String((state.threadState || {}).history_rounds || 0);
      document.getElementById("pendingTurnsValue").textContent = String((state.threadState || {}).pending_turns || 0);
      document.getElementById("memoryValue").textContent = String(state.memoryState || "-");
    }

    function closeSource(){
      if(state.source){
        state.source.close();
        state.source = null;
      }
    }

    function clearRunPanels(){
      closeSource();
      state.runId = "";
      state.resultUrl = "";
      state.eventsUrl = "";
      state.eventCount = 0;
      state.toolCount = 0;
      state.recallMode = "-";
      state.memoryState = "-";
      setStatus("idle");
      setAnswer("等待发起 chat run。", true);
      setSnapshot({});
      setMemoryPill("memory: pending");
      document.getElementById("eventsBox").innerHTML = "";
      updateMetrics();
    }

    function appendEvent(evt){
      state.eventCount += 1;
      if(evt.type === "tool_call") state.toolCount += 1;
      if(evt.type === "recall_started"){
        state.recallMode = String((evt.payload || {}).mode || "-");
      }
      if(evt.type === "memory_capture_updated"){
        const payload = evt.payload || {};
        const mode = String(payload.mode || "-");
        const status = String(payload.status || "-");
        const pending = Number(payload.pending_rounds || 0);
        state.memoryState = mode === "manual" ? `manual:${pending}` : mode;
        setMemoryPill(status === "buffered" ? `memory: buffered (${pending})` : `memory: ${state.memoryState}`);
      }
      if(evt.type === "thread_state_updated"){
        const payload = evt.payload || {};
        if(payload.thread_state && typeof payload.thread_state === "object"){
          setThreadState(payload.thread_state);
        }
      }
      if(evt.type === "assistant_message"){
        setAnswer(String((evt.payload || {}).answer || ""), false);
      }
      if(evt.type === "run_failed"){
        setAnswer(String((evt.payload || {}).error || "run failed"), false);
      }
      updateMetrics();

      const box = document.getElementById("eventsBox");
      const item = document.createElement("div");
      item.className = "event";
      item.innerHTML = `
        <div class="event-head">
          <div class="event-type tag-${esc(evt.type)}">${esc(evt.type)}</div>
          <div class="event-time">${esc(evt.timestamp || "")}</div>
        </div>
      `.trim();
      const pre = document.createElement("pre");
      pre.textContent = pretty(evt.payload || {});
      item.appendChild(pre);
      box.prepend(item);
    }

    async function postJson(path, payload){
      const res = await fetch(joinUrl(path), {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(payload || {})
      });
      const data = await res.json().catch(() => ({}));
      if(!res.ok){
        throw new Error(String(data.error || `request failed: ${res.status}`));
      }
      return data;
    }

    async function fetchSnapshot(){
      if(!state.resultUrl) return;
      try{
        const res = await fetch(joinUrl(state.resultUrl), {cache:"no-store"});
        const data = await res.json().catch(() => ({}));
        setSnapshot(data);
        if(data && data.result && typeof data.result.thread_state === "object"){
          setThreadState(data.result.thread_state);
        }
      }catch(err){
        console.error(err);
      }
    }

    async function fetchThreadState(showAlert=false){
      const threadId = threadIdValue();
      try{
        const res = await fetch(joinUrl(`/v1/chat/threads/${encodeURIComponent(threadId)}/memory/state`), {cache:"no-store"});
        const data = await res.json().catch(() => ({}));
        if(!res.ok){
          throw new Error(String(data.error || `request failed: ${res.status}`));
        }
        setThreadState(data);
        if(showAlert){
          alert(pretty(data));
        }
      }catch(err){
        if(showAlert){
          alert(String(err));
        }
      }
    }

    async function refreshServiceInfo(showAlert=false){
      try{
        const res = await fetch(joinUrl("/healthz"), {cache:"no-store"});
        const data = await res.json().catch(() => ({}));
        const runtime = data && typeof data.runtime === "object" ? data.runtime : {};
        const previousDefault = state.defaultThreadId;
        state.configPath = String(runtime.config_path || "");
        state.defaultThreadId = String(runtime.default_thread_id || "");
        state.persistMemory = runtime.persist_memory === true ? "on" : (runtime.persist_memory === false ? "off" : "-");
        state.serviceState = res.ok ? "ready" : "degraded";
        const threadInput = document.getElementById("threadInput");
        const currentThread = String(threadInput.value || "").trim();
        if(!currentThread || currentThread === previousDefault || currentThread === "demo-thread-1"){
          threadInput.value = state.defaultThreadId || "demo-thread-1";
        }
        updateMetrics();
        await fetchThreadState(false);
        if(showAlert){
          alert(pretty(data));
        }
      }catch(err){
        state.serviceState = "offline";
        state.configPath = "";
        state.defaultThreadId = "";
        state.persistMemory = "";
        updateMetrics();
        if(showAlert){
          alert(String(err));
        }
      }
    }

    function openStream(eventsUrl){
      closeSource();
      const source = new EventSource(joinUrl(eventsUrl));
      state.source = source;
      setStatus("running");

      const handle = (evt) => {
        let payload = {};
        try { payload = JSON.parse(evt.data); } catch { payload = {type: evt.type, payload: evt.data}; }
        appendEvent(payload);
        if(payload.type === "run_completed" || payload.type === "run_failed"){
          setStatus(payload.type === "run_completed" ? "completed" : "failed");
          fetchSnapshot();
          fetchThreadState(false);
          closeSource();
        }
      };

      for(const type of knownTypes){
        source.addEventListener(type, handle);
      }

      source.onerror = () => {
        if(state.source){
          fetchSnapshot();
        }
      };
    }

    async function createRun(){
      const message = String(document.getElementById("messageInput").value || "").trim();
      const threadId = threadIdValue();
      if(!message){
        alert("message 不能为空");
        return;
      }

      clearRunPanels();
      setStatus("starting");
      setAnswer("正在创建 run 并连接 SSE...", true);

      const res = await fetch(joinUrl("/v1/chat/runs"), {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({message, thread_id: threadId})
      });
      const data = await res.json().catch(() => ({}));
      if(!res.ok){
        setStatus("failed");
        setAnswer(String(data.error || `request failed: ${res.status}`), false);
        return;
      }

      state.runId = String(data.run_id || "");
      state.resultUrl = String(data.result_url || "");
      state.eventsUrl = String(data.events_url || "");
      updateMetrics();
      openStream(state.eventsUrl);
    }

    async function setMemoryMode(mode){
      const threadId = threadIdValue();
      const data = await postJson(`/v1/chat/threads/${encodeURIComponent(threadId)}/memory/mode`, {mode});
      if(data && typeof data.thread_state === "object"){
        setThreadState(data.thread_state);
      } else {
        await fetchThreadState(false);
      }
      state.memoryState = mode;
      setMemoryPill(`memory: ${mode}`);
      updateMetrics();
    }

    async function flushThreadMemory(){
      const threadId = threadIdValue();
      const data = await postJson(`/v1/chat/threads/${encodeURIComponent(threadId)}/memory/flush`, {});
      if(data && typeof data.thread_state === "object"){
        setThreadState(data.thread_state);
      } else {
        await fetchThreadState(false);
      }
      const ok = !!data.success && String(data.status || "") !== "failed";
      state.memoryState = ok ? "flushed" : "flush-failed";
      setMemoryPill(ok ? "memory: flushed" : "memory: flush failed");
      updateMetrics();
      alert(pretty(data));
    }

    async function reopenStream(){
      const runId = String(document.getElementById("runInput").value || state.runId || "").trim();
      if(!runId){
        alert("没有可重连的 run_id");
        return;
      }
      state.runId = runId;
      state.eventsUrl = `/v1/chat/runs/${encodeURIComponent(runId)}/events`;
      state.resultUrl = `/v1/chat/runs/${encodeURIComponent(runId)}`;
      updateMetrics();
      openStream(state.eventsUrl);
      fetchSnapshot();
    }

    document.getElementById("sendBtn").addEventListener("click", () => { createRun().catch(err => {
      console.error(err);
      setStatus("failed");
      setAnswer(String(err), false);
    }); });
    document.getElementById("syncBtn").addEventListener("click", () => { refreshServiceInfo(true).catch(console.error); });
    document.getElementById("threadStateBtn").addEventListener("click", () => { fetchThreadState(true).catch(console.error); });
    document.getElementById("manualModeBtn").addEventListener("click", () => { setMemoryMode("manual").catch(err => alert(String(err))); });
    document.getElementById("offModeBtn").addEventListener("click", () => { setMemoryMode("off").catch(err => alert(String(err))); });
    document.getElementById("flushBtn").addEventListener("click", () => { flushThreadMemory().catch(err => alert(String(err))); });
    document.getElementById("apiBaseInput").addEventListener("change", () => { refreshServiceInfo().catch(console.error); });
    document.getElementById("apiBaseInput").addEventListener("blur", () => { refreshServiceInfo().catch(console.error); });
    document.getElementById("threadInput").addEventListener("change", () => { fetchThreadState().catch(console.error); });
    document.getElementById("threadInput").addEventListener("blur", () => { fetchThreadState().catch(console.error); });
    document.getElementById("messageInput").addEventListener("keydown", (event) => {
      if(event.key === "Enter" && !event.shiftKey){
        event.preventDefault();
        createRun().catch(err => {
          console.error(err);
          setStatus("failed");
          setAnswer(String(err), false);
        });
      }
    });

    clearRunPanels();
    refreshServiceInfo();
  </script>
</body>
</html>
"""


def create_handler(*, api_base_url: str):
    html_page = HTML_TEMPLATE.replace("__API_BASE_URL__", api_base_url.rstrip("/"))

    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "MAgentChatAPIDemo/2.0"

        def log_message(self, format: str, *args: Any) -> None:
            logger.info("%s - %s", self.address_string(), format % args)

        def _send_html(self, html_text: str, status: int = 200) -> None:
            encoded = html_text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send_html(html_page)
                return
            if path == "/healthz":
                self._send_json(
                    {
                        "ok": True,
                        "service": "m-agent-chat-api-demo",
                        "api_base_url": api_base_url,
                    }
                )
                return
            self._send_json({"error": "Not found"}, status=404)

    return RequestHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a standalone demo UI for the M-Agent chat API.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8767, help="Bind port. Default: 8767")
    parser.add_argument(
        "--api-base-url",
        default="http://127.0.0.1:8777",
        help="Upstream chat API base URL. Default: http://127.0.0.1:8777",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open the browser.")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    args = parse_args()
    handler_cls = create_handler(api_base_url=str(args.api_base_url or "").strip() or "http://127.0.0.1:8777")
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    url = f"http://{args.host}:{args.port}/"
    logger.info("M-Agent chat API demo listening on %s", url)
    logger.info("Demo will connect to API base %s", args.api_base_url)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("Failed to auto-open browser. Open %s manually.", url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down chat API demo...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
