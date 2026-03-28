#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import traceback
import webbrowser
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import yaml

# Ensure project root is importable when running this file directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from m_agent.agents.memory_agent import create_memory_agent
from m_agent.memory.memory_core.workflow.search.content_search import (
    search_content as workflow_search_content,
)
from m_agent.utils.logging_trace import FunctionTraceHandler, TraceEvent


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("memory.memory_core").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("test_ques_web")

_REFERENCE_CONTENT_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_REFERENCE_CONTENT_LOCK = threading.Lock()


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Memory Agent Web Trace Viewer</title>
  <style>
    :root {
      --bg: #f4efe8;
      --panel: #fffaf3;
      --panel-strong: #f8f0e3;
      --line: #d9ccb5;
      --ink: #1f1a12;
      --muted: #6c624f;
      --accent: #0d6b78;
      --accent-strong: #094d56;
      --good: #2b8a5a;
      --warn: #c57b1f;
      --bad: #ba3c2f;
      --shadow: 0 10px 24px rgba(53, 40, 17, 0.10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(circle at top left, #fbe8cf 0, transparent 34%),
        radial-gradient(circle at top right, #d9efe9 0, transparent 38%),
        var(--bg);
      color: var(--ink);
      min-height: 100vh;
    }
    .shell {
      width: min(96vw, 1800px);
      margin: 0 auto;
      padding: 18px;
    }
    .hero, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: var(--shadow);
    }
    .hero {
      padding: 16px 18px;
      margin-bottom: 12px;
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 14px;
      align-items: stretch;
    }
    .hero h1 {
      margin: 0 0 8px;
      font-size: 28px;
    }
    .hero p {
      margin: 0;
      line-height: 1.55;
      color: var(--muted);
    }
    .hero-note {
      border: 1px dashed var(--line);
      border-radius: 12px;
      background: rgba(255,255,255,0.56);
      padding: 14px;
      font-size: 14px;
      line-height: 1.6;
    }
    .toolbar {
      padding: 14px;
      margin-bottom: 12px;
    }
    .toolbar-grid {
      display: grid;
      grid-template-columns: 1.1fr 180px 1fr auto auto;
      gap: 10px;
      align-items: end;
    }
    label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 5px;
    }
    input[type="text"] {
      width: 100%;
      padding: 10px 11px;
      border: 1px solid var(--line);
      border-radius: 10px;
      font-size: 14px;
      background: white;
      color: var(--ink);
    }
    button {
      border: 0;
      border-radius: 11px;
      padding: 11px 15px;
      font-size: 14px;
      cursor: pointer;
      transition: transform .15s ease, opacity .15s ease, background-color .15s ease;
      white-space: nowrap;
    }
    .primary {
      background: var(--accent);
      color: white;
    }
    .primary:hover { background: var(--accent-strong); transform: translateY(-1px); }
    .secondary {
      background: var(--panel-strong);
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .secondary:hover { opacity: .86; }
    .meta-row {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .metric {
      padding: 12px 14px;
    }
    .metric .k {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }
    .metric .v {
      font-size: 24px;
      font-weight: 700;
      letter-spacing: .25px;
    }
    .status-running { color: var(--warn); }
    .status-done { color: var(--good); }
    .status-failed { color: var(--bad); }
    .layout {
      display: grid;
      grid-template-columns: minmax(760px, 1.28fr) minmax(460px, .92fr);
      gap: 12px;
      min-height: 72vh;
    }
    .panel {
      padding: 14px;
    }
    .panel h2 {
      margin: 0 0 10px;
      font-size: 17px;
    }
    .section-label {
      font-size: 11px;
      letter-spacing: .14em;
      text-transform: uppercase;
      color: #8e7d61;
      margin-bottom: 8px;
    }
    .timeline {
      max-height: calc(72vh - 70px);
      overflow: auto;
      overscroll-behavior: contain;
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding-right: 4px;
    }
    .trace-panel {
      padding: 14px;
      display: flex;
      flex-direction: column;
      min-height: 72vh;
    }
    .trace-stack {
      display: grid;
      grid-template-rows: minmax(260px, .82fr) minmax(420px, 1.18fr);
      gap: 12px;
      min-height: 0;
      flex: 1 1 auto;
    }
    .trace-section {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .trace-head {
      margin: 0 0 8px;
      font-size: 15px;
      font-weight: 700;
    }
    .trace-section .timeline,
    .trace-section .tool-list {
      flex: 1 1 auto;
      max-height: none;
    }
    .workspace-pane,
    .detail-pane {
      min-height: 72vh;
      display: flex;
      flex-direction: column;
    }
    .workspace-head,
    .detail-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 12px;
    }
    .workspace-question {
      max-width: 440px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255,255,255,.72);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      padding: 5px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--panel-strong);
      font-size: 12px;
      color: var(--muted);
    }
    .timeline-main {
      flex: 1 1 auto;
      max-height: none;
      min-height: 0;
      padding-right: 6px;
    }
    .timeline-item {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 13px;
      background: white;
      cursor: pointer;
      position: relative;
      transition: border-color .16s ease, transform .16s ease, box-shadow .16s ease, background-color .16s ease;
    }
    .timeline-item::before {
      content: "";
      position: absolute;
      left: -7px;
      top: 18px;
      width: 10px;
      height: 10px;
      border-radius: 999px;
      border: 2px solid white;
      background: #d5b57d;
      box-shadow: 0 0 0 1px rgba(213,181,125,.4);
    }
    .timeline-item:hover {
      border-color: #bea882;
      transform: translateX(2px);
      box-shadow: 0 10px 18px rgba(59, 43, 18, .08);
    }
    .timeline-item.active {
      border-color: var(--accent);
      box-shadow: inset 0 0 0 1px rgba(13,107,120,.12);
      background: #f4fbfc;
    }
    .timeline-item.active::before {
      background: var(--accent);
      box-shadow: 0 0 0 1px rgba(13,107,120,.28);
    }
    .timeline-top {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      border: 1px solid var(--line);
      background: var(--panel-strong);
    }
    .badge.api_call { color: #0b5f6c; }
    .badge.api_response { color: #2b8a5a; }
    .badge.tool_call_detail { color: #7b4a00; }
    .badge.tool_result_detail { color: #7b4a00; }
    .badge.subq_done { color: #2b8a5a; }
    .badge.subq_start { color: #c57b1f; }
    .badge.error { color: var(--bad); }
    .timeline-title {
      font-size: 14px;
      font-weight: 700;
      margin-bottom: 4px;
      word-break: break-word;
    }
    .timeline-summary {
      font-size: 13px;
      color: var(--muted);
      line-height: 1.45;
      word-break: break-word;
    }
    .right-col {
      display: grid;
      grid-template-rows: auto auto auto minmax(260px, 1fr);
      gap: 12px;
      min-height: 0;
    }
    .duo {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      min-height: 0;
    }
    .section-block {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: white;
      padding: 10px 11px;
      min-height: 0;
    }
    .readable-box {
      display: flex;
      flex-direction: column;
      gap: 8px;
      min-height: 64px;
    }
    .readable-root {
      display: flex;
      flex-direction: column;
      gap: 8px;
      min-width: 0;
    }
    .kv-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
      min-width: 0;
    }
    .kv-row {
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(248, 240, 227, 0.48);
      padding: 9px 10px;
      min-width: 0;
    }
    .kv-key {
      font-size: 12px;
      font-weight: 700;
      color: var(--muted);
      line-height: 1.5;
      word-break: break-word;
    }
    .kv-val {
      min-width: 0;
    }
    .scalar-block {
      font-size: 13px;
      line-height: 1.6;
      color: var(--ink);
      word-break: break-word;
    }
    .value-empty {
      color: var(--muted);
      font-style: italic;
    }
    .chip-wrap {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .value-chip {
      display: inline-flex;
      align-items: center;
      max-width: 100%;
      padding: 4px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: white;
      font-size: 12px;
      color: #3a3124;
      word-break: break-word;
    }
    .value-chip.placeholder {
      color: #7b4a00;
      background: #fff6dd;
      border-color: #e8cf90;
    }
    .nested-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: white;
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      min-width: 0;
    }
    .nested-card-head {
      font-size: 12px;
      font-weight: 700;
      color: var(--muted);
      letter-spacing: .15px;
    }
    .data-panel {
      max-height: 280px;
      overflow: auto;
      overscroll-behavior: contain;
      padding-right: 4px;
    }
    .pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      line-height: 1.55;
      color: #2a2419;
      max-height: 260px;
      overflow: auto;
      overscroll-behavior: contain;
    }
    .subq-list, .tool-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
      overflow: auto;
      overscroll-behavior: contain;
    }
    .subq-list { max-height: 220px; }
    .tool-list { max-height: 240px; }
    .subq-item, .tool-item {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: white;
      padding: 10px 11px;
    }
    .subq-item .head, .tool-item summary {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      font-size: 13px;
      font-weight: 600;
    }
    .status-chip {
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      border: 1px solid var(--line);
      background: var(--panel-strong);
    }
    .status-chip.pending { color: var(--muted); }
    .status-chip.in_progress { color: var(--warn); }
    .status-chip.completed { color: var(--good); }
    .status-chip.failed { color: var(--bad); }
    .answer {
      margin-top: 7px;
      font-size: 12px;
      color: var(--muted);
      line-height: 1.5;
      word-break: break-word;
    }
    details.tool-item > summary {
      cursor: pointer;
      list-style: none;
    }
    details.tool-item > summary::-webkit-details-marker { display: none; }
    .tool-grid {
      margin-top: 8px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    .tool-grid .label {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 3px;
    }
    .tool-panel {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .tool-panel .tool-list {
      flex: 1 1 auto;
      min-height: 380px;
      max-height: none;
      padding-right: 4px;
    }
    .result-panel .readable-box {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .detail-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      justify-content: flex-end;
      align-items: flex-start;
    }
    .detail-scroll {
      flex: 1 1 auto;
      min-height: 0;
      overflow: auto;
      overscroll-behavior: contain;
      padding-right: 6px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .detail-pane > :not(.detail-head):not(.detail-scroll) {
      display: none !important;
    }
    .detail-card {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: white;
      padding: 14px;
      box-shadow: 0 8px 18px rgba(53, 40, 17, .05);
    }
    .detail-card h3 {
      margin: 0 0 10px;
      font-size: 14px;
    }
    .lead-block {
      font-size: 15px;
      line-height: 1.7;
      color: #2d261b;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .info-tile {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(248, 240, 227, .44);
      padding: 10px 11px;
      min-width: 0;
    }
    .stack,
    .ref-list,
    .chip-row {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .chip-row {
      flex-direction: row;
      flex-wrap: wrap;
      gap: 6px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      padding: 4px 9px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: white;
      font-size: 12px;
      color: #3a3124;
      max-width: 100%;
      word-break: break-word;
    }
    .mini-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(255,255,255,.82);
      padding: 10px 11px;
      min-width: 0;
    }
    .mini-card-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 6px;
    }
    .mini-card-title {
      font-size: 11px;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: #8a775c;
      margin-bottom: 6px;
    }
    .mini-card-head .mini-card-title {
      margin-bottom: 0;
    }
    .link-button {
      border: 1px solid var(--line);
      background: white;
      color: var(--accent);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      line-height: 1;
      cursor: pointer;
      white-space: nowrap;
    }
    .link-button[disabled] {
      opacity: .6;
      cursor: wait;
    }
    .ref-dialogue {
      margin-top: 10px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .info-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: #8a775c;
      margin-bottom: 6px;
    }
    .info-value {
      font-size: 13px;
      line-height: 1.55;
      word-break: break-word;
      color: var(--ink);
    }
    .kv-item {
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
      padding: 9px 0;
      border-bottom: 1px dashed rgba(217, 204, 181, .8);
    }
    .kv-item:last-child {
      border-bottom: 0;
      padding-bottom: 0;
    }
    .kv-value,
    .muted {
      color: var(--muted);
    }
    .text-block {
      color: var(--ink);
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.65;
    }
    .turn-list,
    .match-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .turn-card,
    .match-card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fffdf8;
      padding: 12px;
    }
    .turn-head,
    .match-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 8px;
      font-size: 12px;
      color: var(--muted);
    }
    .turn-speaker,
    .match-title {
      font-weight: 700;
      color: #2f281d;
    }
    .turn-text,
    .match-main,
    .detail-note {
      font-size: 14px;
      line-height: 1.65;
      white-space: pre-wrap;
      word-break: break-word;
      color: #2f281d;
    }
    .match-sub {
      margin-top: 8px;
      font-size: 13px;
      line-height: 1.6;
      color: var(--muted);
      white-space: pre-wrap;
      word-break: break-word;
    }
    .code-block {
      margin: 0;
      padding: 12px;
      border-radius: 14px;
      background: #f6f1e9;
      border: 1px solid #e6d8be;
      font-size: 12px;
      line-height: 1.6;
      white-space: pre-wrap;
      word-break: break-word;
      overflow: auto;
    }
    .empty-hero {
      flex: 1 1 auto;
      border: 1px dashed var(--line);
      border-radius: 18px;
      background: rgba(255,255,255,.54);
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.7;
      padding: 24px;
      text-align: center;
    }
    .empty {
      color: var(--muted);
      font-size: 13px;
      padding: 8px 2px;
    }
    @media (max-width: 1380px) {
      .layout, .hero, .toolbar-grid, .duo, .meta-row {
        grid-template-columns: 1fr;
      }
      .timeline { max-height: 42vh; }
      .right-col { grid-template-rows: auto; }
      .kv-row { grid-template-columns: 1fr; }
      .workspace-head,
      .detail-head {
        flex-direction: column;
      }
      .detail-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div>
        <h1>Memory Agent Web Trace Viewer</h1>
        <p>
          杩欎釜椤甸潰浼氭妸涓€娆￠棶绛旈噷鐨勫畬鏁存墽琛岃繃绋嬫媶寮€鏄剧ず锛氶棶棰樼瓥鐣ャ€佹槸鍚﹀厛鐩寸瓟銆?
          鍘熷鍒嗚В璁″垝銆佸瓙闂鎵ц銆佹瘡涓€姝?API 璋冪敤銆佹瘡娆″伐鍏峰叆鍙傚拰杩斿洖鎽樿锛屼互鍙婃渶缁堢粨鏋?JSON銆?
        </p>
      </div>
      <div class="hero-note">
        <div><strong>閫傚悎鎺掓煡锛?/strong></div>
        <div>1. 涓轰粈涔堜細鍒嗚В / 涓嶅垎瑙?/div>
        <div>2. 鏌愪釜瀛愰棶棰樺崱鍦ㄥ摢涓€姝?/div>
        <div>3. 鏌愪釜宸ュ叿鍒板簳鎷垮埌浜嗕粈涔堝弬鏁板拰杩斿洖</div>
        <div>4. 鏈€缁堢瓟妗堟槸鐢卞摢鍑犳璇佹嵁鎷煎嚭鏉ョ殑</div>
      </div>
    </section>

    <section class="panel toolbar">
      <div class="toolbar-grid">
        <div>
          <label for="configInput">Config</label>
          <input id="configInput" type="text" value="config\prompt\agent_sys.yaml" />
        </div>
        <div>
          <label for="threadInput">Thread ID</label>
          <input id="threadInput" type="text" value="memory-agent-1" />
        </div>
        <div>
          <label for="questionInput">Question</label>
          <input id="questionInput" type="text" value="When is Jon's group performing at a festival?" />
        </div>
        <button class="primary" id="runBtn">Run Query</button>
        <button class="secondary" id="resetBtn">Reset View</button>
      </div>
    </section>

    <section class="meta-row">
      <div class="panel metric"><div class="k">Status</div><div class="v" id="statusValue">Ready</div></div>
      <div class="panel metric"><div class="k">Events</div><div class="v" id="eventCount">0</div></div>
      <div class="panel metric"><div class="k">API Traces</div><div class="v" id="apiCount">0</div></div>
      <div class="panel metric"><div class="k">Tool Calls</div><div class="v" id="toolCount">0</div></div>
      <div class="panel metric"><div class="k">Sub-questions</div><div class="v" id="subqCount">0</div></div>
    </section>

    <section class="layout">
      <section class="panel workspace-pane">
        <div class="workspace-head">
          <div>
            <div class="section-label">Execution Workspace</div>
            <h2>Execution Timeline</h2>
          </div>
          <div>
            <div id="workspaceStatus" class="status-pill">Ready</div>
            <div id="workspaceQuestion" class="workspace-question" style="margin-top:8px;">Run a question to populate the workspace.</div>
          </div>
        </div>
        <div id="timeline" class="timeline timeline-main"></div>
      </section>

      <section class="panel detail-pane">
        <div class="detail-head">
          <div>
            <div class="section-label">Selected Step</div>
            <h2 id="detailTitle">No step selected</h2>
          </div>
          <div id="detailMeta" class="detail-meta"></div>
        </div>
        <div id="detailContent" class="detail-scroll">
          <div class="empty-hero">
            Run a query, then click a timeline step on the left to inspect what the agent did and what each tool returned.
          </div>
        </div>
        <div class="duo">
          <section class="panel">
            <h2>Question Strategy</h2>
            <div class="section-block"><div id="strategyBox" class="readable-box"><div class="empty">Waiting鈥?/div></div></div>
          </section>
          <section class="panel">
            <h2>Execution Outcome</h2>
            <div class="section-block"><div id="summaryBox" class="readable-box"><div class="empty">Waiting鈥?/div></div></div>
          </section>
        </div>

        <div class="duo">
          <section class="panel">
            <h2>Question Plan</h2>
            <div class="section-block"><div id="planBox" class="readable-box"><div class="empty">Waiting for plan鈥?/div></div></div>
          </section>
          <section class="panel">
            <h2>Direct Answer Snapshot</h2>
            <div class="section-block"><div id="directBox" class="readable-box"><div class="empty">No direct-answer payload yet.</div></div></div>
          </section>
        </div>

        <section class="panel">
          <h2>Sub-question Board</h2>
          <div id="subqBoard" class="subq-list"></div>
        </section>

        <section class="panel result-panel">
          <h2>Final Result</h2>
          <div class="section-block"><div id="resultBox" class="readable-box"><div class="empty">Waiting for result鈥?/div></div></div>
        </section>
      </section>
    </section>
  </div>

  <script>
    const stateUrl = "/api/state";
    const runUrl = "/api/run";
    const resetUrl = "/api/reset";
    const contentRefUrl = "/api/content-ref";
    let selectedEventId = null;
    let pinnedEventSelection = false;
    let lastState = null;
    let lastTimelineSignature = "";
    let lastRenderedSelectedId = null;
    let referenceContentCache = new Map();
    let referenceContentInflight = new Map();
    let toolOpenState = new Map();
    let toolScrollState = new Map();
    let lastToolSignature = "";

    function escapeHtml(text) {
      return String(text ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function pretty(value) {
      if (value === undefined || value === null || value === "") return "";
      if (typeof value === "string") return value;
      try {
        return JSON.stringify(value, null, 2);
      } catch (_) {
        return String(value);
      }
    }

    function isPlainObject(value) {
      return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function titleCaseKey(key) {
      return String(key || "")
        .replace(/_/g, " ")
        .replace(/\b([a-z])/g, (_, ch) => ch.toUpperCase());
    }

    function orderedKeys(value) {
      const priority = [
        "question",
        "question_type",
        "strategy",
        "route",
        "answer",
        "gold_answer",
        "status",
        "plan_summary",
        "decomposition_reason",
        "summary",
        "reason",
        "dialogue_id",
        "episode_id",
        "tool_name",
        "call_id",
        "error",
        "evidence",
        "params",
        "result",
      ];
      const rank = new Map(priority.map((key, index) => [key, index]));
      return Object.keys(value).sort((a, b) => {
        const ar = rank.has(a) ? rank.get(a) : 999;
        const br = rank.has(b) ? rank.get(b) : 999;
        if (ar !== br) return ar - br;
        return a.localeCompare(b);
      });
    }

    function renderScalar(value) {
      if (value === undefined || value === null || value === "") {
        return '<span class="value-empty">-</span>';
      }
      if (value === "<max_depth>") {
        return '<span class="value-chip placeholder" title="This trace omits deeper nested containers after a depth limit.">Deep content omitted</span>';
      }
      return escapeHtml(String(value));
    }

    function renderReadableValue(value, label = "", depth = 0) {
      if (value === undefined || value === null || value === "") {
        return '<div class="scalar-block"><span class="value-empty">-</span></div>';
      }

      if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
        return `<div class="scalar-block">${renderScalar(value)}</div>`;
      }

      if (Array.isArray(value)) {
        if (!value.length) {
          return '<div class="empty">Empty list.</div>';
        }
        const allPrimitive = value.every(item => (
          item === undefined
          || item === null
          || typeof item === "string"
          || typeof item === "number"
          || typeof item === "boolean"
        ));
        if (allPrimitive) {
          return `
            <div class="chip-wrap">
              ${value.map(item => `<span class="value-chip">${renderScalar(item)}</span>`).join("")}
            </div>
          `;
        }
        return `
          <div class="readable-root">
            ${value.map((item, index) => `
              <div class="nested-card">
                <div class="nested-card-head">${escapeHtml(titleCaseKey(label) || "Item")} ${index + 1}</div>
                ${renderReadableValue(item, label, depth + 1)}
              </div>
            `).join("")}
          </div>
        `;
      }

      if (isPlainObject(value)) {
        const keys = orderedKeys(value);
        if (!keys.length) {
          return '<div class="empty">Empty object.</div>';
        }
        return `
          <div class="kv-list">
            ${keys.map(key => `
              <div class="kv-row">
                <div class="kv-key">${escapeHtml(titleCaseKey(key))}</div>
                <div class="kv-val">${renderReadableValue(value[key], key, depth + 1)}</div>
              </div>
            `).join("")}
          </div>
        `;
      }

      return `<div class="scalar-block">${escapeHtml(String(value))}</div>`;
    }

    function setReadableBox(id, value, emptyText) {
      const box = document.getElementById(id);
      if (value === undefined || value === null || value === "") {
        box.innerHTML = `<div class="empty">${escapeHtml(emptyText)}</div>`;
        return;
      }
      box.innerHTML = renderReadableValue(value);
    }

    function hasMeaningfulValue(value) {
      if (value === undefined || value === null) return false;
      if (typeof value === "string") return value.trim() !== "";
      if (Array.isArray(value)) return value.some(item => hasMeaningfulValue(item));
      if (isPlainObject(value)) return Object.values(value).some(item => hasMeaningfulValue(item));
      return true;
    }

    function compactText(value, maxLen = 220) {
      const text = String(value ?? "").replace(/\s+/g, " ").trim();
      if (!text) return "";
      if (text.length <= maxLen) return text;
      return text.slice(0, Math.max(0, maxLen - 3)) + "...";
    }

    function pickMeaningful(entries) {
      const out = {};
      Object.entries(entries || {}).forEach(([key, value]) => {
        if (hasMeaningfulValue(value)) {
          out[key] = value;
        }
      });
      return out;
    }

    function firstDefined(...values) {
      for (const value of values) {
        if (hasMeaningfulValue(value)) return value;
      }
      return null;
    }

    function summarizeTimeRange(value) {
      if (!hasMeaningfulValue(value)) return null;
      if (Array.isArray(value) && value.length === 2) {
        return pickMeaningful({ start_time: value[0], end_time: value[1] });
      }
      if (isPlainObject(value)) {
        return pickMeaningful({
          start_time: firstDefined(value.start_time, value.start, value.from),
          end_time: firstDefined(value.end_time, value.end, value.to),
        });
      }
      return compactText(value, 160);
    }

    function summarizeTurn(turn) {
      if (!isPlainObject(turn)) return compactText(turn, 220);
      return pickMeaningful({
        speaker: firstDefined(turn.speaker, turn.role, turn.name, turn.participant, turn.author),
        timestamp: firstDefined(turn.timestamp, turn.time),
        text: compactText(firstDefined(turn.text, turn.content, turn.utterance, turn.message, turn.value), 260),
      });
    }

    function summarizeTurns(turns) {
      if (!Array.isArray(turns) || !turns.length) return null;
      return turns.map(item => summarizeTurn(item)).filter(item => hasMeaningfulValue(item));
    }

    function summarizeEvidenceRefs(value) {
      if (!hasMeaningfulValue(value)) return null;
      const refs = Array.isArray(value) ? value : [value];
      const mapped = refs.map(ref => {
        if (!isPlainObject(ref)) return compactText(ref, 140);
        return pickMeaningful({
          dialogue_id: ref.dialogue_id,
          episode_id: ref.episode_id,
          fact_id: ref.fact_id,
          scene_id: ref.scene_id,
          turn_span: ref.turn_span,
          event_time: summarizeTimeRange(firstDefined(ref.event_time, ref.time)),
          fact: compactText(firstDefined(ref["Atomic fact"], ref.atomic_fact, ref.fact, ref.fact_text), 220),
          text: compactText(firstDefined(ref.text, ref.quote, ref.excerpt, ref.original_text), 220),
        });
      }).filter(item => hasMeaningfulValue(item));
      return mapped.length ? mapped : null;
    }

    function summarizeEvidence(value, depth = 0) {
      if (!hasMeaningfulValue(value)) return null;
      if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
        return compactText(value, 220);
      }
      if (Array.isArray(value)) {
        const items = value
          .map(item => summarizeEvidence(item, depth + 1))
          .filter(item => hasMeaningfulValue(item));
        return items.length ? items : null;
      }
      if (!isPlainObject(value)) {
        return compactText(String(value), 220);
      }
      const contentList = Array.isArray(value.content)
        ? value.content.map(item => compactText(item, 200)).filter(Boolean)
        : null;
      const turns = summarizeTurns(value.turns);
      const actualTime = Array.isArray(value.actual_time)
        ? value.actual_time.map(item => summarizeTimeRange(item)).filter(item => hasMeaningfulValue(item))
        : null;
      const sourceRefs = summarizeEvidenceRefs(firstDefined(value.evidence, value.sources, value.source));
      const preferred = pickMeaningful({
        fact: compactText(firstDefined(value["Atomic fact"], value.atomic_fact, value.fact, value.fact_text), 220),
        field: value.field,
        content: contentList,
        event: compactText(value.event, 220),
        text: compactText(firstDefined(value.text, value.excerpt, value.quote, value.original_text), 240),
        abstract_time: value.abstract_time,
        actual_time: actualTime,
        turn_span: value.turn_span,
        event_time: summarizeTimeRange(firstDefined(value.event_time, value.time)),
        turn_time_span: summarizeTimeRange(value.turn_time_span),
        scene_id: firstDefined(value.scene_id, value.scene, value.event_info && value.event_info.scene_id),
        scene_theme: firstDefined(value.theme, value.scene_theme, value.event_info && value.event_info.scene_theme),
        participants: Array.isArray(value.participants)
          ? value.participants.map(item => compactText(item, 120)).filter(Boolean)
          : null,
        turns: turns,
        score: value.score,
        similarity: value.similarity,
        dialogue_id: value.dialogue_id,
        episode_id: value.episode_id,
        source_refs: sourceRefs,
        error: compactText(value.error, 180),
      });
      if (Object.keys(preferred).length) {
        return preferred;
      }
      if (depth >= 2) {
        return "Deep evidence object omitted";
      }
      const out = {};
      for (const key of Object.keys(value).slice(0, 4)) {
        const summarized = summarizeEvidence(value[key], depth + 1);
        if (hasMeaningfulValue(summarized)) out[key] = summarized;
      }
      return Object.keys(out).length ? out : "Evidence available";
    }

    function summarizeToolParams(params) {
      if (!hasMeaningfulValue(params)) return null;
      if (!isPlainObject(params)) return compactText(params, 220);
      const preferred = pickMeaningful({
        query: compactText(firstDefined(params.query, params.question, params.keyword, params.text), 180),
        dialogue_id: params.dialogue_id,
        episode_id: params.episode_id,
        entity_id: params.entity_id,
        entity_name: params.entity_name,
        feature: params.feature,
        relation: params.relation,
        event: params.event,
        start_time: params.start_time,
        end_time: params.end_time,
        top_k: params.top_k,
      });
      if (Object.keys(preferred).length) return preferred;
      return pickMeaningful(
        Object.fromEntries(
          Object.entries(params)
            .slice(0, 4)
            .map(([key, value]) => [key, compactText(typeof value === "string" ? value : pretty(value), 120)])
        )
      );
    }

    function summarizeToolResult(result) {
      if (!hasMeaningfulValue(result)) return null;
      if (typeof result === "string" || typeof result === "number" || typeof result === "boolean") {
        return compactText(result, 240);
      }
      if (Array.isArray(result)) {
        return pickMeaningful({
          item_count: result.length,
          matches: result.map(item => summarizeEvidence(item)).filter(item => hasMeaningfulValue(item)),
        });
      }
      if (!isPlainObject(result)) {
        return compactText(String(result), 240);
      }
      const listValue = firstDefined(result.results, result.items, result.records, result.hits, result.candidates, result.data);
      const turns = summarizeTurns(result.turns);
      const matches = Array.isArray(listValue)
        ? listValue.map(item => summarizeEvidence(item)).filter(item => hasMeaningfulValue(item))
        : null;
      const preferred = pickMeaningful({
        hit: result.hit,
        status: result.status,
        answer: compactText(result.answer, 220),
        gold_answer: result.gold_answer,
        summary: compactText(result.summary, 220),
        count: firstDefined(result.matched_count, result.count, result.total, Array.isArray(listValue) ? listValue.length : null),
        dialogue_id: result.dialogue_id,
        episode_id: result.episode_id,
        turn_span: result.turn_span,
        event_time: summarizeTimeRange(result.event_time),
        turn_time_span: summarizeTimeRange(result.turn_time_span),
        event_info: summarizeEvidence(result.event_info),
        participants: Array.isArray(result.participants)
          ? result.participants.map(item => compactText(item, 120)).filter(Boolean)
          : null,
        turns: turns,
        matches: matches,
        evidence: matches ? null : summarizeEvidence(result.evidence),
        excerpt: compactText(firstDefined(result.excerpt, result.text, typeof result.content === "string" ? result.content : null, result.original_text), 220),
        error: compactText(result.error, 180),
      });
      if (Object.keys(preferred).length) return preferred;
      return "Result returned";
    }

    function summarizePlan(plan) {
      if (!isPlainObject(plan)) return null;
      return pickMeaningful({
        goal: compactText(plan.goal, 220),
        question_type: plan.question_type,
        strategy: firstDefined(plan.strategy, plan.route),
        decomposition_reason: compactText(plan.decomposition_reason, 240),
        sub_questions: Array.isArray(plan.sub_questions)
          ? plan.sub_questions.map(item => compactText(item, 180)).filter(Boolean)
          : null,
      });
    }

    function summarizeDirectPayload(payload) {
      if (!isPlainObject(payload)) return null;
      return pickMeaningful({
        answer: compactText(payload.answer, 320),
        gold_answer: payload.gold_answer,
        evidence: summarizeEvidence(payload.evidence),
      });
    }

    function summarizeResultState(state) {
      const result = state.result || state.final_answer_payload || {};
      const plan = (result && result.question_plan) || state.live_plan || null;
      const subResults = Array.isArray(result.sub_question_results) && result.sub_question_results.length
        ? result.sub_question_results
        : (state.live_sub_questions || []);
      const summarizedSubResults = subResults
        .map(item => pickMeaningful({
          index: item.index,
          question: compactText(item.question, 180),
          answer: compactText(item.answer, 220),
          status: item.status,
        }))
        .filter(item => Object.keys(item).length);
      return pickMeaningful({
        answer: compactText(firstDefined(result.answer, state.final_answer_payload && state.final_answer_payload.answer), 420),
        gold_answer: firstDefined(result.gold_answer, state.final_answer_payload && state.final_answer_payload.gold_answer),
        route: summarizedSubResults.length ? "decomposed" : "direct",
        question_type: plan && plan.question_type,
        plan_summary: compactText(firstDefined(result.plan_summary, state.final_answer_payload && state.final_answer_payload.plan_summary), 260),
        evidence: summarizeEvidence(firstDefined(result.evidence, state.final_answer_payload && state.final_answer_payload.evidence)),
        sub_question_results: summarizedSubResults.length ? summarizedSubResults : null,
        error: compactText(state.error_text, 260),
      });
    }

    function renderCard(title, bodyHtml) {
      return `
        <div class="nested-card">
          <div class="nested-card-head">${escapeHtml(title)}</div>
          ${bodyHtml}
        </div>
      `;
    }

    function statusClass(status) {
      const normalized = String(status || "").toLowerCase();
      if (normalized.includes("running")) return "status-running";
      if (normalized.includes("done")) return "status-done";
      if (normalized.includes("failed")) return "status-failed";
      return "";
    }

    function renderMetrics(state) {
      const statusValue = document.getElementById("statusValue");
      statusValue.textContent = state.status || "Ready";
      statusValue.className = "v " + statusClass(state.status || "");
      document.getElementById("eventCount").textContent = String((state.events || []).length);
      document.getElementById("apiCount").textContent = String((state.events || []).filter(e => e.kind === "api_call" || e.kind === "api_response").length);
      document.getElementById("toolCount").textContent = String((state.tool_steps || []).length);
      document.getElementById("subqCount").textContent = String((state.live_sub_questions || []).length);
    }

    function kindClass(kind) {
      return String(kind || "log").toLowerCase().replace(/[^a-z0-9_]+/g, "_");
    }

    function kindLabel(kind) {
      const labels = {
        question_strategy: "Question Strategy",
        plan_update: "Question Plan",
        subq_start: "Sub-question Start",
        subq_done: "Sub-question Done",
        tool_call_detail: "Tool Call",
        tool_result_detail: "Tool Result",
        direct_answer_payload: "Direct Answer",
        direct_answer_fallback: "Direct Fallback",
        final_answer_payload: "Final Answer",
        api_call: "API Call",
        api_response: "API Response",
        error: "Error",
        log: "Log",
      };
      return labels[kind] || titleCaseKey(kind || "log");
    }

    function renderChip(label) {
      return `<span class="chip">${escapeHtml(String(label))}</span>`;
    }

    function renderCodeBlock(text) {
      return `<pre class="code-block">${escapeHtml(String(text ?? ""))}</pre>`;
    }

    function renderPrettyValue(value, depth = 0) {
      if (!hasMeaningfulValue(value)) {
        return '<span class="muted">-</span>';
      }
      if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
        return `<div class="text-block">${escapeHtml(String(value))}</div>`;
      }
      if (Array.isArray(value)) {
        if (!value.length) return '<span class="muted">-</span>';
        const allPrimitive = value.every(item => (
          item === undefined
          || item === null
          || typeof item === "string"
          || typeof item === "number"
          || typeof item === "boolean"
        ));
        if (allPrimitive) {
          return `<div class="chip-row">${value.map(item => renderChip(item)).join("")}</div>`;
        }
        return `
          <div class="stack">
            ${value.map((item, index) => `
              <div class="mini-card">
                <div class="mini-card-title">Item ${index + 1}</div>
                ${renderPrettyValue(item, depth + 1)}
              </div>
            `).join("")}
          </div>
        `;
      }
      if (isPlainObject(value)) {
        const keys = orderedKeys(value).filter(key => hasMeaningfulValue(value[key]));
        if (!keys.length) return '<span class="muted">-</span>';
        return `
          <div class="stack">
            ${keys.map(key => `
              <div class="kv-item">
                <div class="kv-key">${escapeHtml(titleCaseKey(key))}</div>
                <div class="kv-value">${renderPrettyValue(value[key], depth + 1)}</div>
              </div>
            `).join("")}
          </div>
        `;
      }
      return `<div class="text-block">${escapeHtml(String(value))}</div>`;
    }

    function renderInfoValue(value) {
      if (!hasMeaningfulValue(value)) return '<span class="muted">-</span>';
      if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
        return escapeHtml(String(value));
      }
      if (Array.isArray(value)) {
        const allPrimitive = value.every(item => (
          item === undefined
          || item === null
          || typeof item === "string"
          || typeof item === "number"
          || typeof item === "boolean"
        ));
        if (allPrimitive) {
          return `<div class="chip-row">${value.map(item => renderChip(item)).join("")}</div>`;
        }
      }
      return renderPrettyValue(value);
    }

    function renderKvGrid(entries) {
      const rows = Object.entries(entries || {}).filter(([, value]) => hasMeaningfulValue(value));
      if (!rows.length) return '<div class="muted">No structured fields.</div>';
      return `
        <div class="detail-grid">
          ${rows.map(([key, value]) => `
            <div class="info-tile">
              <div class="info-label">${escapeHtml(titleCaseKey(key))}</div>
              <div class="info-value">${renderInfoValue(value)}</div>
            </div>
          `).join("")}
        </div>
      `;
    }

    function renderDetailCard(title, bodyHtml) {
      return `
        <section class="detail-card">
          <h3>${escapeHtml(title)}</h3>
          ${bodyHtml}
        </section>
      `;
    }

    function renderSummaryCard(text) {
      return renderDetailCard("Summary", `<div class="lead-block">${escapeHtml(String(text || ""))}</div>`);
    }

    function renderWorkspaceMeta(state) {
      const statusChip = document.getElementById("workspaceStatus");
      statusChip.textContent = state.status || "Ready";
      statusChip.className = "status-pill " + statusClass(state.status || "");
      document.getElementById("workspaceQuestion").textContent = state.question
        ? compactText(state.question, 260)
        : "Run a question to populate the workspace.";
    }

    function renderReferenceList(value, title = "Evidence References") {
      const refs = summarizeEvidenceRefs(value);
      if (!Array.isArray(refs) || !refs.length) return "";
      return renderDetailCard(
        title,
        `<div class="ref-list">${refs.map((ref, index) => `
          <div class="mini-card">
            <div class="mini-card-head">
              <div class="mini-card-title">Reference ${index + 1}</div>
              ${isPlainObject(ref) && ref.dialogue_id && ref.episode_id ? `
                <button
                  class="link-button ref-load-btn"
                  type="button"
                  data-dialogue-id="${escapeHtml(String(ref.dialogue_id))}"
                  data-episode-id="${escapeHtml(String(ref.episode_id))}"
                  data-ref-key="${escapeHtml(String(ref.dialogue_id) + "::" + String(ref.episode_id))}"
                >${referenceContentCache.has(referenceKey(ref.dialogue_id, ref.episode_id)) ? "Reload Dialogue" : "Refresh Dialogue"}</button>
              ` : ""}
            </div>
            ${isPlainObject(ref) ? renderKvGrid(ref) : `<div class="text-block">${escapeHtml(String(ref))}</div>`}
            ${isPlainObject(ref) && ref.dialogue_id && ref.episode_id ? `
              <div class="ref-dialogue" data-ref-slot="${escapeHtml(String(ref.dialogue_id) + "::" + String(ref.episode_id))}">
                ${referenceContentCache.has(referenceKey(ref.dialogue_id, ref.episode_id))
                  ? renderInlineDialogueContent(referenceContentCache.get(referenceKey(ref.dialogue_id, ref.episode_id)))
                  : '<div class="muted">Resolving referenced dialogue content...</div>'}
              </div>
            ` : ""}
          </div>
        `).join("")}</div>`
      );
    }

    function renderTurnsCard(turns, title = "Dialogue Content") {
      const items = summarizeTurns(turns);
      if (!Array.isArray(items) || !items.length) return "";
      return renderDetailCard(
        title,
        `<div class="turn-list">${items.map((turn, index) => `
          <article class="turn-card">
            <div class="turn-head">
              <span class="turn-speaker">${escapeHtml(String(firstDefined(turn.speaker, "Turn " + (index + 1))))}</span>
              ${turn.timestamp ? `<span>${escapeHtml(String(turn.timestamp))}</span>` : ""}
            </div>
            <div class="turn-text">${escapeHtml(String(turn.text || ""))}</div>
          </article>
        `).join("")}</div>`
      );
    }

    function referenceKey(dialogueId, episodeId) {
      return `${String(dialogueId || "")}::${String(episodeId || "")}`;
    }

    function renderInlineDialogueContent(content) {
      if (!hasMeaningfulValue(content)) {
        return '<div class="muted">No dialogue content available.</div>';
      }
      const parts = [];
      const overview = pickMeaningful({
        dialogue_id: content.dialogue_id,
        episode_id: content.episode_id,
        turn_span: hasMeaningfulValue(content.turn_span) ? pretty(content.turn_span) : null,
        event_time: hasMeaningfulValue(content.event_time) ? pretty(summarizeTimeRange(content.event_time)) : null,
        turn_time_span: hasMeaningfulValue(content.turn_time_span) ? pretty(summarizeTimeRange(content.turn_time_span)) : null,
        scene_id: firstDefined(content.source && content.source.scene_id, content.event_info && content.event_info.scene_id),
        source: content.source && content.source.from,
      });
      if (Object.keys(overview).length) {
        parts.push(renderKvGrid(overview));
      }
      const turnsHtml = renderTurnsCard(content.turns, "Resolved Dialogue");
      if (turnsHtml) {
        parts.push(turnsHtml);
      }
      if (hasMeaningfulValue(content.event_info)) {
        parts.push(renderDetailCard("Event Info", renderPrettyValue(content.event_info)));
      }
      if (!turnsHtml && hasMeaningfulValue(content.error)) {
        parts.push(renderDetailCard("Error", renderCodeBlock(content.error)));
      }
      if (!parts.length) {
        parts.push(renderPrettyValue(content));
      }
      return parts.join("");
    }

    async function fetchReferenceContent(config, dialogueId, episodeId, options = {}) {
      const key = referenceKey(dialogueId, episodeId);
      const forceReload = options && options.forceReload === true;
      if (!forceReload && referenceContentCache.has(key)) {
        return referenceContentCache.get(key);
      }
      if (!forceReload && referenceContentInflight.has(key)) {
        return referenceContentInflight.get(key);
      }
      const requestPromise = postJson(contentRefUrl, {
        config: config || "",
        dialogue_id: dialogueId,
        episode_id: episodeId,
      }).then(response => {
        const result = response.result || {};
        referenceContentCache.set(key, result);
        return result;
      });
      referenceContentInflight.set(key, requestPromise);
      try {
        return await requestPromise;
      } finally {
        if (referenceContentInflight.get(key) === requestPromise) {
          referenceContentInflight.delete(key);
        }
      }
    }

    async function loadReferenceIntoSlot(button, slot, state, options = {}) {
      if (!button || !slot) return;
      const dialogueId = button.getAttribute("data-dialogue-id") || "";
      const episodeId = button.getAttribute("data-episode-id") || "";
      const key = button.getAttribute("data-ref-key") || referenceKey(dialogueId, episodeId);
      const forceReload = options && options.forceReload === true;

      if (!forceReload && referenceContentCache.has(key)) {
        slot.innerHTML = renderInlineDialogueContent(referenceContentCache.get(key));
        button.textContent = "Reload Dialogue";
        return;
      }

      button.disabled = true;
      button.textContent = forceReload ? "Refreshing..." : "Loading...";
      slot.innerHTML = '<div class="muted">Loading dialogue content...</div>';
      try {
        const content = await fetchReferenceContent(state && state.config, dialogueId, episodeId, { forceReload });
        slot.innerHTML = renderInlineDialogueContent(content);
        button.textContent = "Reload Dialogue";
      } catch (error) {
        slot.innerHTML = `<div class="muted">${escapeHtml(error.message || String(error))}</div>`;
        button.textContent = "Retry Dialogue";
      } finally {
        button.disabled = false;
      }
    }

    function attachReferenceLoaders(state) {
      const container = document.getElementById("detailContent");
      if (!container) return;
      container.querySelectorAll(".ref-load-btn").forEach(button => {
        if (button.dataset.bound === "1") return;
        button.dataset.bound = "1";
        const dialogueId = button.getAttribute("data-dialogue-id") || "";
        const episodeId = button.getAttribute("data-episode-id") || "";
        const key = button.getAttribute("data-ref-key") || referenceKey(dialogueId, episodeId);
        const slot = Array.from(container.querySelectorAll("[data-ref-slot]"))
          .find(node => node.getAttribute("data-ref-slot") === key);

        button.addEventListener("click", async () => {
          if (!slot) return;
          await loadReferenceIntoSlot(button, slot, state, { forceReload: true });
        });
        if (!slot) return;
        void loadReferenceIntoSlot(button, slot, state, { forceReload: false });
      });
    }

    function extractMatchList(result) {
      if (!hasMeaningfulValue(result)) return [];
      if (Array.isArray(result)) return result;
      if (!isPlainObject(result)) return [];
      for (const key of ["results", "items", "records", "hits", "candidates", "data"]) {
        if (Array.isArray(result[key])) return result[key];
      }
      return [];
    }

    function matchHeadline(match, index) {
      const headline = firstDefined(
        match["Atomic fact"],
        match.atomic_fact,
        match.fact,
        match.fact_text,
        match.event,
        match.text,
        match.quote,
        match.excerpt,
        Array.isArray(match.content) ? match.content.join("; ") : null,
        match.summary
      );
      return compactText(headline || ("Match " + (index + 1)), 340);
    }

    function renderMatchCard(match, index) {
      const meta = pickMeaningful({
        field: match.field,
        score: match.score,
        similarity: match.similarity,
        scene_id: firstDefined(match.scene_id, match.scene),
        dialogue_id: match.dialogue_id,
        episode_id: match.episode_id,
        abstract_time: hasMeaningfulValue(match.abstract_time) ? pretty(match.abstract_time) : null,
        actual_time: hasMeaningfulValue(match.actual_time) ? pretty(match.actual_time) : null,
      });
      const sections = [
        `<div class="match-main">${escapeHtml(matchHeadline(match, index))}</div>`,
      ];
      if (Array.isArray(match.content) && match.content.length) {
        sections.push(`<div class="chip-row">${match.content.map(item => renderChip(item)).join("")}</div>`);
      }
      const extraText = firstDefined(match.text, match.quote, match.excerpt);
      if (hasMeaningfulValue(extraText) && compactText(extraText, 340) !== matchHeadline(match, index)) {
        sections.push(`<div class="match-sub">${escapeHtml(compactText(extraText, 520))}</div>`);
      }
      if (Object.keys(meta).length) {
        sections.push(renderKvGrid(meta));
      }
      const turnsCard = renderTurnsCard(match.turns, "Matched Dialogue");
      if (turnsCard) sections.push(turnsCard);
      const refsCard = renderReferenceList(match.evidence);
      if (refsCard) sections.push(refsCard);
      return `
        <article class="match-card">
          <div class="match-head">
            <span class="match-title">Match ${index + 1}</span>
          </div>
          ${sections.join("")}
        </article>
      `;
    }

    function renderToolCallSection(payload) {
      const sections = [];
      sections.push(renderDetailCard("Tool Call", renderKvGrid({
        tool: payload.tool_name,
        call_id: payload.call_id,
        status: payload.status,
        timestamp: payload.ts,
      })));
      if (hasMeaningfulValue(payload.params)) {
        sections.push(renderDetailCard("Input Params", renderPrettyValue(payload.params)));
      }
      return sections.join("");
    }

    function renderToolResultSection(payload) {
      const result = payload.result;
      const sections = [];
      sections.push(renderDetailCard("Tool Result", renderKvGrid({
        tool: payload.tool_name,
        call_id: payload.call_id,
        status: payload.status,
        timestamp: payload.ts,
      })));
      if (hasMeaningfulValue(payload.params)) {
        sections.push(renderDetailCard("Input Params", renderPrettyValue(payload.params)));
      }
      if (payload.error) {
        sections.push(renderDetailCard("Error", renderCodeBlock(payload.error)));
        return sections.join("");
      }
      if (!hasMeaningfulValue(result)) {
        sections.push(renderDetailCard("Returned Payload", '<div class="muted">No result payload.</div>'));
        return sections.join("");
      }

      const overview = pickMeaningful({
        hit: result.hit,
        matched_count: firstDefined(result.matched_count, result.count, result.total),
        dialogue_id: result.dialogue_id,
        episode_id: result.episode_id,
        turn_span: hasMeaningfulValue(result.turn_span) ? pretty(result.turn_span) : null,
        event_time: hasMeaningfulValue(result.event_time) ? pretty(summarizeTimeRange(result.event_time)) : null,
        turn_time_span: hasMeaningfulValue(result.turn_time_span) ? pretty(summarizeTimeRange(result.turn_time_span)) : null,
        scene_id: firstDefined(result.scene_id, result.event_info && result.event_info.scene_id),
        scene_theme: firstDefined(result.scene_theme, result.event_info && result.event_info.scene_theme),
      });
      if (Object.keys(overview).length) {
        sections.push(renderDetailCard("Returned Overview", renderKvGrid(overview)));
      }

      const answerText = firstDefined(result.answer, result.summary);
      if (hasMeaningfulValue(answerText)) {
        sections.push(renderDetailCard("Returned Answer", `<div class="lead-block">${escapeHtml(String(answerText))}</div>`));
      }

      if (Array.isArray(result.participants) && result.participants.length) {
        sections.push(renderDetailCard("Participants", `<div class="chip-row">${result.participants.map(item => renderChip(item)).join("")}</div>`));
      }

      const turnsCard = renderTurnsCard(result.turns, "Dialogue Content");
      if (turnsCard) {
        sections.push(turnsCard);
      }

      const matches = extractMatchList(result);
      if (matches.length) {
        sections.push(renderDetailCard(
          `Returned Matches (${matches.length})`,
          `<div class="match-list">${matches.map((match, index) => renderMatchCard(match, index)).join("")}</div>`
        ));
      }

      if (hasMeaningfulValue(result.event_info)) {
        sections.push(renderDetailCard("Event Info", renderPrettyValue(result.event_info)));
      }

      const refsCard = renderReferenceList(firstDefined(result.evidence, result.sources));
      if (refsCard) {
        sections.push(refsCard);
      } else if (hasMeaningfulValue(result.source)) {
        sections.push(renderDetailCard("Source", renderPrettyValue(result.source)));
      }

      const extraText = firstDefined(
        result.original_text,
        result.excerpt,
        result.quote,
        typeof result.content === "string" ? result.content : null,
        result.text
      );
      if (hasMeaningfulValue(extraText) && !turnsCard && !matches.length) {
        sections.push(renderDetailCard("Returned Content", `<div class="lead-block">${escapeHtml(String(extraText))}</div>`));
      }

      if (!turnsCard && !matches.length && !hasMeaningfulValue(answerText) && !Object.keys(overview).length && !hasMeaningfulValue(extraText)) {
        sections.push(renderDetailCard("Returned Payload", renderPrettyValue(result)));
      }
      return sections.join("");
    }

    function renderQuestionStrategySection(payload, state) {
      return renderDetailCard("Strategy", renderKvGrid({
        question: compactText(state.question, 260),
        route: payload.decompose_first === true ? "decompose_first" : payload.decompose_first === false ? "direct_first" : null,
        reason: payload.reason,
        suggested_tool_order: payload.suggested_tool_order,
      }));
    }

    function renderPlanSection(payload) {
      const sections = [
        renderDetailCard("Plan Overview", renderKvGrid({
          goal: payload.goal,
          question_type: payload.question_type,
          strategy: firstDefined(payload.strategy, payload.route),
          decomposition_reason: payload.decomposition_reason,
        })),
      ];
      if (Array.isArray(payload.sub_questions) && payload.sub_questions.length) {
        sections.push(renderDetailCard(
          `Sub-questions (${payload.sub_questions.length})`,
          `<div class="match-list">${payload.sub_questions.map((item, index) => `
            <article class="match-card">
              <div class="match-head"><span class="match-title">Sub-question ${index + 1}</span></div>
              <div class="match-main">${escapeHtml(String(item))}</div>
            </article>
          `).join("")}</div>`
        ));
      }
      return sections.join("");
    }

    function renderSubQuestionSection(payload) {
      const sections = [
        renderDetailCard("Sub-question", renderKvGrid({
          index: payload.index,
          status: payload.status,
          question: payload.question,
          gold_answer: payload.gold_answer,
        })),
      ];
      if (hasMeaningfulValue(payload.answer)) {
        sections.push(renderDetailCard("Answer", `<div class="lead-block">${escapeHtml(String(payload.answer))}</div>`));
      }
      if (hasMeaningfulValue(payload.evidence)) {
        sections.push(renderDetailCard("Evidence", renderPrettyValue(payload.evidence)));
      }
      if (hasMeaningfulValue(payload.error)) {
        sections.push(renderDetailCard("Error", renderCodeBlock(payload.error)));
      }
      return sections.join("");
    }

    function renderAnswerSection(payload, title) {
      const sections = [
        renderDetailCard(title, renderKvGrid({
          gold_answer: payload.gold_answer,
          plan_summary: payload.plan_summary,
        })),
      ];
      if (hasMeaningfulValue(payload.answer)) {
        sections.push(renderDetailCard("Answer", `<div class="lead-block">${escapeHtml(String(payload.answer))}</div>`));
      }
      if (hasMeaningfulValue(payload.evidence)) {
        sections.push(renderDetailCard("Evidence", renderPrettyValue(payload.evidence)));
      }
      if (hasMeaningfulValue(payload.question_plan)) {
        sections.push(renderDetailCard("Question Plan", renderPrettyValue(payload.question_plan)));
      }
      if (Array.isArray(payload.sub_question_results) && payload.sub_question_results.length) {
        sections.push(renderDetailCard("Sub-question Results", renderPrettyValue(payload.sub_question_results)));
      }
      return sections.join("");
    }

    function renderApiTraceSection(event) {
      const sections = [
        renderDetailCard("Trace Meta", renderKvGrid({
          function_name: event.function_name,
          phase: event.phase,
          logger_name: event.logger_name,
          level_name: event.level_name,
        })),
      ];
      sections.push(renderDetailCard("Message", renderCodeBlock(event.raw_message || event.summary || "")));
      if (hasMeaningfulValue(event.payload)) {
        sections.push(renderDetailCard("Payload", renderPrettyValue(event.payload)));
      }
      return sections.join("");
    }

    function renderFallbackSection(payload) {
      return renderDetailCard("Fallback", renderPrettyValue(payload));
    }

    function renderErrorSection(event) {
      return renderDetailCard("Error", renderCodeBlock(event.raw_message || event.summary || ""));
    }

    function renderStrategy(state) {
      const strategy = state.question_strategy || {};
      const payload = pickMeaningful({
        question: compactText(state.question, 220),
        route: Object.keys(strategy).length ? (strategy.decompose_first ? "decompose_first" : "direct_first") : null,
        reason: compactText(firstDefined(strategy.reason, strategy.decomposition_reason), 240),
      });
      setReadableBox("strategyBox", payload, "Waiting for question strategy...");
    }

    function renderSummary(state) {
      const plan = state.live_plan || (state.result || {}).question_plan || null;
      const payload = pickMeaningful({
        status: state.status,
        route: Array.isArray((plan || {}).sub_questions) && plan.sub_questions.length ? "decomposed" : "direct",
        question_type: plan && plan.question_type,
        fallback_reason: isPlainObject(state.direct_answer_fallback)
          ? compactText(state.direct_answer_fallback.reason, 220)
          : compactText(state.direct_answer_fallback, 220),
        error: compactText(state.error_text, 220),
      });
      setReadableBox(
        "summaryBox",
        Object.keys(payload).length ? payload : null,
        "No execution outcome yet."
      );
    }

    function renderPlan(state) {
      const plan = state.live_plan || (state.result || {}).question_plan || null;
      setReadableBox("planBox", summarizePlan(plan), "Waiting for plan...");
    }

    function renderDirect(state) {
      const directPayload = state.direct_answer_payload || null;
      setReadableBox("directBox", summarizeDirectPayload(directPayload), "No direct-answer payload yet.");
    }

    function renderSubQuestions(state) {
      const board = document.getElementById("subqBoard");
      const items = state.live_sub_questions || [];
      if (!items.length) {
        board.innerHTML = '<div class="empty">No sub-questions yet. If this stays empty and you still get a result, the agent probably answered directly.</div>';
        return;
      }

      board.innerHTML = items.map(item => {
        const status = String(item.status || "pending").toLowerCase();
        const evidence = summarizeEvidence(item.evidence);
        return `
          <div class="subq-item">
            <div class="head">
              <div>#${escapeHtml(item.index)} ${escapeHtml(item.question || "")}</div>
              <span class="status-chip ${escapeHtml(status)}">${escapeHtml(status)}</span>
            </div>
            ${item.answer ? `<div class="answer"><strong>Answer:</strong> ${escapeHtml(compactText(item.answer, 240))}</div>` : ""}
            ${evidence ? `<div class="answer"><strong>Evidence:</strong></div><div class="data-panel">${renderReadableValue(evidence, "evidence")}</div>` : ""}
            ${item.error ? `<div class="answer"><strong>Error:</strong> ${escapeHtml(compactText(item.error, 180))}</div>` : ""}
          </div>
        `;
      }).join("");
    }

    function captureToolScrollState(board) {
      const next = new Map();
      if (!board) return next;
      next.set("__board__", { top: board.scrollTop, left: board.scrollLeft });
      board.querySelectorAll("[data-scroll-key]").forEach(node => {
        const key = node.getAttribute("data-scroll-key");
        if (!key) return;
        next.set(key, { top: node.scrollTop, left: node.scrollLeft });
      });
      return next;
    }

    function restoreToolScrollState(board, snapshot) {
      if (!board || !snapshot) return;
      const boardState = snapshot.get("__board__");
      if (boardState) {
        board.scrollTop = boardState.top || 0;
        board.scrollLeft = boardState.left || 0;
      }
      board.querySelectorAll("[data-scroll-key]").forEach(node => {
        const key = node.getAttribute("data-scroll-key");
        if (!key || !snapshot.has(key)) return;
        const saved = snapshot.get(key) || {};
        node.scrollTop = saved.top || 0;
        node.scrollLeft = saved.left || 0;
      });
    }

    function renderTools(state) {
      const board = document.getElementById("toolBoard");
      const tools = state.tool_steps || [];
      const signature = JSON.stringify(tools);
      if (signature === lastToolSignature) {
        return;
      }

      const previousScrollState = captureToolScrollState(board);
      if (!tools.length) {
        board.innerHTML = '<div class="empty">No tool calls yet.</div>';
        toolScrollState = new Map();
        lastToolSignature = signature;
        return;
      }

      const activeToolIds = new Set(tools.map(tool => String(tool.call_id)));
      for (const key of Array.from(toolOpenState.keys())) {
        if (!activeToolIds.has(String(key))) {
          toolOpenState.delete(key);
        }
      }

      board.innerHTML = tools.map(tool => {
        const callId = String(tool.call_id);
        const status = String(tool.status || "started");
        const defaultOpen = status !== "completed";
        const isOpen = toolOpenState.has(callId) ? toolOpenState.get(callId) : defaultOpen;
        const summarizedParams = summarizeToolParams(tool.params);
        const summarizedResult = summarizeToolResult(tool.result || tool.error || "");
        return `
          <details class="tool-item" data-call-id="${escapeHtml(callId)}" ${isOpen ? "open" : ""}>
            <summary>
              <span>#${escapeHtml(tool.call_id)} ${escapeHtml(tool.tool_name || "")}</span>
              <span class="status-chip ${escapeHtml(status)}">${escapeHtml(status)}</span>
            </summary>
            <div class="tool-grid">
              <div>
                <div class="label">Params</div>
                <div class="data-panel" data-scroll-key="${escapeHtml(callId + ":params")}">
                  ${summarizedParams ? renderReadableValue(summarizedParams, "params") : '<div class="empty">No params.</div>'}
                </div>
              </div>
              <div>
                <div class="label">Result</div>
                <div class="data-panel" data-scroll-key="${escapeHtml(callId + ":result")}">
                  ${summarizedResult ? renderReadableValue(summarizedResult, "result") : '<div class="empty">No result yet.</div>'}
                </div>
              </div>
            </div>
          </details>
        `;
      }).join("");

      board.querySelectorAll("details.tool-item").forEach(node => {
        node.addEventListener("toggle", () => {
          const callId = String(node.getAttribute("data-call-id") || "");
          if (!callId) return;
          toolOpenState.set(callId, node.open);
        });
      });

      toolScrollState = previousScrollState;
      restoreToolScrollState(board, toolScrollState);
      lastToolSignature = signature;
    }

    function kindBadge(kind) {
      const safeKind = kindClass(kind);
      return `<span class="badge ${escapeHtml(safeKind)}">${escapeHtml(kindLabel(kind))}</span>`;
    }

    function renderTimeline(state) {
      const timeline = document.getElementById("timeline");
      const events = state.events || [];
      if (!events.length) {
        timeline.innerHTML = '<div class="empty-hero">No trace events yet.</div>';
        lastTimelineSignature = "";
        lastRenderedSelectedId = null;
        return;
      }

      const validIds = new Set(events.map(item => item.id));
      if (!pinnedEventSelection || selectedEventId === null || !validIds.has(selectedEventId)) {
        selectedEventId = events[events.length - 1].id;
      }

      const signature = JSON.stringify(events.map(item => [item.id, item.kind, item.title, item.summary, item.timestamp]));
      if (signature === lastTimelineSignature && lastRenderedSelectedId === selectedEventId) {
        return;
      }

      const previousScrollTop = timeline.scrollTop;
      timeline.innerHTML = events.map(item => `
        <div class="timeline-item ${item.id === selectedEventId ? "active" : ""}" data-id="${item.id}">
          <div class="timeline-top">
            <span>#${item.id} ${escapeHtml(item.timestamp || "")}</span>
            ${kindBadge(item.kind || "log")}
          </div>
          <div class="timeline-title">${escapeHtml(item.title || "")}</div>
          <div class="timeline-summary">${escapeHtml(item.summary || "")}</div>
        </div>
      `).join("");

      timeline.querySelectorAll(".timeline-item").forEach(node => {
        node.addEventListener("click", () => {
          const nextId = Number(node.getAttribute("data-id"));
          if (!Number.isFinite(nextId)) return;
          selectedEventId = nextId;
          pinnedEventSelection = true;
          renderTimeline(lastState || state);
          renderResult(lastState || state);
        });
      });

      timeline.scrollTop = previousScrollTop;
      lastTimelineSignature = signature;
      lastRenderedSelectedId = selectedEventId;
    }

    function renderResult(state) {
      const titleEl = document.getElementById("detailTitle");
      const metaEl = document.getElementById("detailMeta");
      const contentEl = document.getElementById("detailContent");
      const events = state.events || [];
      if (!events.length) {
        titleEl.textContent = "No step selected";
        metaEl.innerHTML = "";
        contentEl.innerHTML = '<div class="empty-hero">Run a query, then click a timeline step on the left to inspect what the agent did and what each tool returned.</div>';
        return;
      }

      const selected = events.find(item => item.id === selectedEventId) || events[events.length - 1];
      selectedEventId = selected.id;
      titleEl.textContent = selected.title || "Step Detail";
      metaEl.innerHTML = [
        renderChip("#" + selected.id),
        kindBadge(selected.kind || "log"),
        selected.timestamp ? renderChip(selected.timestamp) : "",
        selected.function_name ? renderChip(selected.function_name) : "",
      ].join("");

      const payload = isPlainObject(selected.payload) ? selected.payload : {};
      const sections = [renderSummaryCard(selected.summary || selected.raw_message || "")];

      if (selected.kind === "tool_call_detail") {
        sections.push(renderToolCallSection(payload));
      } else if (selected.kind === "tool_result_detail") {
        sections.push(renderToolResultSection(payload));
      } else if (selected.kind === "question_strategy") {
        sections.push(renderQuestionStrategySection(payload, state));
      } else if (selected.kind === "plan_update") {
        sections.push(renderPlanSection(payload));
      } else if (selected.kind === "subq_start" || selected.kind === "subq_done") {
        sections.push(renderSubQuestionSection(payload));
      } else if (selected.kind === "direct_answer_payload") {
        sections.push(renderAnswerSection(payload, "Direct Answer Payload"));
      } else if (selected.kind === "final_answer_payload") {
        sections.push(renderAnswerSection(payload, "Final Answer Payload"));
      } else if (selected.kind === "direct_answer_fallback") {
        sections.push(renderFallbackSection(payload));
      } else if (selected.kind === "api_call" || selected.kind === "api_response") {
        sections.push(renderApiTraceSection(selected));
      } else if (selected.kind === "error") {
        sections.push(renderErrorSection(selected));
      } else if (hasMeaningfulValue(selected.payload)) {
        sections.push(renderDetailCard("Payload", renderPrettyValue(selected.payload)));
      } else if (hasMeaningfulValue(selected.raw_message)) {
        sections.push(renderDetailCard("Raw Message", renderCodeBlock(selected.raw_message)));
      }

      contentEl.innerHTML = sections.join("");
      attachReferenceLoaders(state);
    }

    function renderAll(state) {
      lastState = state;
      renderMetrics(state);
      renderWorkspaceMeta(state);
      renderTimeline(state);
      renderResult(state);
    }

    async function fetchState() {
      const response = await fetch(stateUrl, { cache: "no-store" });
      if (!response.ok) throw new Error("Failed to fetch state");
      return response.json();
    }

    async function pollLoop() {
      try {
        const state = await fetchState();
        renderAll(state);
      } catch (error) {
        console.error(error);
      } finally {
        window.setTimeout(pollLoop, 600);
      }
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || ("Request failed: " + response.status));
      }
      return data;
    }

    async function runQuery() {
      const payload = {
        config: document.getElementById("configInput").value,
        thread_id: document.getElementById("threadInput").value,
        question: document.getElementById("questionInput").value,
      };
      try {
        selectedEventId = null;
        pinnedEventSelection = false;
        lastState = null;
        lastTimelineSignature = "";
        lastRenderedSelectedId = null;
        referenceContentCache = new Map();
        referenceContentInflight = new Map();
        toolOpenState = new Map();
        toolScrollState = new Map();
        lastToolSignature = "";
        await postJson(runUrl, payload);
      } catch (error) {
        alert(error.message || String(error));
      }
    }

    async function resetView() {
      try {
        selectedEventId = null;
        pinnedEventSelection = false;
        lastState = null;
        lastTimelineSignature = "";
        lastRenderedSelectedId = null;
        referenceContentCache = new Map();
        referenceContentInflight = new Map();
        toolOpenState = new Map();
        toolScrollState = new Map();
        lastToolSignature = "";
        await postJson(resetUrl, {});
      } catch (error) {
        alert(error.message || String(error));
      }
    }

    document.getElementById("runBtn").addEventListener("click", runQuery);
    document.getElementById("resetBtn").addEventListener("click", resetView);
    pollLoop();
  </script>
</body>
</html>
"""


class TraceRunState:
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
        self._event_id = 0
        self._reset_locked()

    def _reset_locked(self) -> None:
        self.running = False
        self.status = "Ready"
        self.config = r"config\prompt\agent_sys.yaml"
        self.thread_id = "memory-agent-1"
        self.question = ""
        self.events: list[dict[str, Any]] = []
        self.live_plan: dict[str, Any] = {}
        self.live_sub_questions: list[dict[str, Any]] = []
        self.question_strategy: dict[str, Any] = {}
        self.direct_answer_payload: dict[str, Any] | None = None
        self.direct_answer_fallback: dict[str, Any] | None = None
        self.final_answer_payload: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None
        self.error_text: str | None = None
        self._tool_steps_by_id: dict[int, dict[str, Any]] = {}

    def reset(self) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("A run is still in progress.")
            self._event_id = 0
            self._reset_locked()

    def begin_run(self, config: str, thread_id: str, question: str) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("A run is already in progress.")
            self._event_id = 0
            self._reset_locked()
            self.running = True
            self.status = "Running"
            self.config = config
            self.thread_id = thread_id
            self.question = question

    def set_result(self, result: dict[str, Any]) -> None:
        with self._lock:
            self.result = deepcopy(result)
            if not self.final_answer_payload:
                self.final_answer_payload = deepcopy(result)
            question_plan = result.get("question_plan")
            if isinstance(question_plan, dict) and not self.live_plan:
                self.live_plan = deepcopy(question_plan)
            sub_question_results = result.get("sub_question_results")
            if (
                isinstance(sub_question_results, list)
                and sub_question_results
                and not self.live_sub_questions
            ):
                self.live_sub_questions = [
                    deepcopy(item) for item in sub_question_results if isinstance(item, dict)
                ]
            self.status = "Done"
            self.running = False

    def set_error(self, error_text: str) -> None:
        with self._lock:
            self.error_text = error_text
            self.status = "Failed"
            self.running = False

    def append_trace(self, event: TraceEvent) -> None:
        with self._lock:
            event_item = self._build_event_item(event)
            self.events.append(event_item)
            self._apply_event(event_item)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            tool_steps = [
                deepcopy(self._tool_steps_by_id[key]) for key in sorted(self._tool_steps_by_id)
            ]
            return {
                "running": self.running,
                "status": self.status,
                "config": self.config,
                "thread_id": self.thread_id,
                "question": self.question,
                "events": deepcopy(self.events),
                "live_plan": deepcopy(self.live_plan),
                "live_sub_questions": deepcopy(self.live_sub_questions),
                "question_strategy": deepcopy(self.question_strategy),
                "direct_answer_payload": deepcopy(self.direct_answer_payload),
                "direct_answer_fallback": deepcopy(self.direct_answer_fallback),
                "final_answer_payload": deepcopy(self.final_answer_payload),
                "tool_steps": tool_steps,
                "result": deepcopy(self.result),
                "error_text": self.error_text,
            }

    def _next_event_id(self) -> int:
        self._event_id += 1
        return self._event_id

    @staticmethod
    def _load_json_payload(raw_message: str, prefix: str) -> Any:
        payload_text = raw_message[len(prefix) :].strip()
        try:
            return json.loads(payload_text)
        except Exception:
            return payload_text

    def _build_event_item(self, event: TraceEvent) -> dict[str, Any]:
        raw = event.raw_message or ""
        payload: Any = None
        kind = "log"
        title = event.function_name or event.logger_name
        summary = event.detail or raw

        if raw.startswith(self.QUESTION_STRATEGY_PREFIX):
            kind = "question_strategy"
            title = "Question Strategy"
            payload = self._load_json_payload(raw, self.QUESTION_STRATEGY_PREFIX)
            summary = self._question_strategy_summary(payload)
        elif raw.startswith(self.PLAN_UPDATE_PREFIX):
            kind = "plan_update"
            title = "Question Plan"
            payload = self._load_json_payload(raw, self.PLAN_UPDATE_PREFIX)
            summary = self._plan_summary(payload)
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
            summary = self._answer_summary(payload)
        elif raw.startswith(self.DIRECT_FALLBACK_PREFIX):
            kind = "direct_answer_fallback"
            title = "Direct Answer Fallback"
            payload = self._load_json_payload(raw, self.DIRECT_FALLBACK_PREFIX)
            summary = self._fallback_summary(payload)
        elif raw.startswith(self.FINAL_PAYLOAD_PREFIX):
            kind = "final_answer_payload"
            title = "Final Answer Payload"
            payload = self._load_json_payload(raw, self.FINAL_PAYLOAD_PREFIX)
            summary = self._answer_summary(payload)
        elif event.function_name:
            kind = "api_call" if (event.phase or "").upper() == "CALL" else "api_response"
            title = f"{event.function_name} {kind.replace('_', ' ').title()}"
            summary = event.detail or raw
        elif event.level_name == "ERROR":
            kind = "error"
            title = event.logger_name

        return {
            "id": self._next_event_id(),
            "timestamp": event.timestamp,
            "logger_name": event.logger_name,
            "level_name": event.level_name,
            "phase": event.phase,
            "function_name": event.function_name,
            "raw_message": raw,
            "payload": payload,
            "kind": kind,
            "title": title,
            "summary": summary,
        }

    @staticmethod
    def _question_strategy_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        return (
            f"decompose_first={payload.get('decompose_first')} | "
            f"reason={payload.get('reason', '')}"
        )

    @staticmethod
    def _plan_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        sub_questions = payload.get("sub_questions", [])
        count = len(sub_questions) if isinstance(sub_questions, list) else 0
        return f"{payload.get('question_type', 'unknown')} | {count} sub-question(s)"

    @staticmethod
    def _subq_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        index = payload.get("index")
        question = str(payload.get("question", "") or "").strip()
        status = str(payload.get("status", "") or "").strip()
        answer = str(payload.get("answer", "") or "").strip()
        summary = f"#{index} [{status}] {question}"
        if answer:
            summary += f" -> {answer[:120]}"
        return summary

    @staticmethod
    def _tool_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        call_id = payload.get("call_id")
        tool_name = payload.get("tool_name", "")
        params = payload.get("params", {})
        if isinstance(params, dict) and params:
            first_key = next(iter(params))
            first_value = params[first_key]
            return f"#{call_id} {tool_name}({first_key}={first_value})"
        return f"#{call_id} {tool_name}"

    @staticmethod
    def _tool_result_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        call_id = payload.get("call_id")
        tool_name = payload.get("tool_name", "")
        status = payload.get("status", "")
        return f"#{call_id} {tool_name} -> {status}"

    @staticmethod
    def _answer_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        answer = str(payload.get("answer", "") or "").strip()
        gold_answer = payload.get("gold_answer")
        return f"gold_answer={gold_answer!r} | answer={answer[:140]}"

    @staticmethod
    def _fallback_summary(payload: Any) -> str:
        if isinstance(payload, dict):
            return str(payload.get("reason", "") or "")
        return str(payload)

    def _apply_event(self, event_item: dict[str, Any]) -> None:
        kind = event_item["kind"]
        payload = event_item.get("payload")

        if kind == "question_strategy" and isinstance(payload, dict):
            self.question_strategy = deepcopy(payload)
            return
        if kind == "plan_update" and isinstance(payload, dict):
            self.live_plan = deepcopy(payload)
            self._initialize_live_sub_questions()
            return
        if kind == "subq_start":
            self._apply_sub_question_update(payload, default_status="in_progress")
            return
        if kind == "subq_done":
            self._apply_sub_question_update(payload, default_status="completed")
            return
        if kind == "tool_call_detail" and isinstance(payload, dict):
            call_id = self._tool_call_id(payload)
            if call_id is not None:
                self._tool_steps_by_id[call_id] = deepcopy(payload)
            return
        if kind == "tool_result_detail" and isinstance(payload, dict):
            call_id = self._tool_call_id(payload)
            if call_id is not None:
                current = self._tool_steps_by_id.get(call_id, {})
                current.update(deepcopy(payload))
                self._tool_steps_by_id[call_id] = current
            return
        if kind == "direct_answer_payload" and isinstance(payload, dict):
            self.direct_answer_payload = deepcopy(payload)
            return
        if kind == "direct_answer_fallback":
            self.direct_answer_fallback = deepcopy(payload)
            return
        if kind == "final_answer_payload" and isinstance(payload, dict):
            self.final_answer_payload = deepcopy(payload)

    @staticmethod
    def _tool_call_id(payload: dict[str, Any]) -> Optional[int]:
        try:
            return int(payload.get("call_id"))
        except Exception:
            return None

    def _initialize_live_sub_questions(self) -> None:
        sub_questions = self.live_plan.get("sub_questions", [])
        if not isinstance(sub_questions, list):
            self.live_sub_questions = []
            return
        self.live_sub_questions = []
        for idx, item in enumerate(sub_questions, start=1):
            text = str(item).strip()
            if not text:
                continue
            self.live_sub_questions.append(
                {
                    "index": idx,
                    "question": text,
                    "status": "pending",
                    "answer": "",
                }
            )

    def _apply_sub_question_update(self, payload: Any, default_status: str) -> None:
        if not isinstance(payload, dict):
            return
        try:
            index_int = int(payload.get("index"))
        except Exception:
            return

        while len(self.live_sub_questions) < index_int:
            self.live_sub_questions.append(
                {
                    "index": len(self.live_sub_questions) + 1,
                    "question": "",
                    "status": "pending",
                    "answer": "",
                }
            )

        item = self.live_sub_questions[index_int - 1]
        item["index"] = index_int
        if payload.get("question") is not None:
            item["question"] = str(payload.get("question") or "")
        item["status"] = str(payload.get("status") or default_status)
        if payload.get("answer") is not None:
            item["answer"] = str(payload.get("answer") or "")
        if payload.get("evidence") is not None:
            item["evidence"] = payload.get("evidence")
        if payload.get("error") is not None:
            item["error"] = str(payload.get("error") or "")


def run_agent_once(state: TraceRunState, config_text: str, question: str, thread_id: str) -> None:
    trace_logger = logging.getLogger("Agents.memory_agent")
    trace_handler = FunctionTraceHandler(
        callback=state.append_trace,
        include_non_api=True,
    )
    trace_logger.addHandler(trace_handler)
    trace_logger.setLevel(logging.INFO)

    try:
        config_path = Path(config_text)
        if not config_path.is_absolute():
            config_path = PROJECT_ROOT / config_path
        agent = create_memory_agent(config_path=config_path)
        result = agent.ask(question=question, thread_id=thread_id or None)
        state.set_result(result)
    except Exception:
        state.set_error(traceback.format_exc())
    finally:
        trace_logger.removeHandler(trace_handler)


def _resolve_config_path(config_text: str) -> Path:
    config_path = Path(config_text or r"config\prompt\agent_sys.yaml")
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    return config_path


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def _resolve_reference_search_paths(config_path: Path) -> tuple[Path, Path, Path]:
    agent_cfg = _load_yaml_dict(config_path)
    memory_cfg_raw = agent_cfg.get("memory_core_config_path")
    if not isinstance(memory_cfg_raw, str) or not memory_cfg_raw.strip():
        raise ValueError("memory_core_config_path is required in agent config")

    memory_cfg_path = Path(memory_cfg_raw.strip())
    if not memory_cfg_path.is_absolute():
        memory_cfg_path = (config_path.parent / memory_cfg_path).resolve()

    memory_cfg = _load_yaml_dict(memory_cfg_path)
    workflow_id = str(memory_cfg.get("workflow_id", "") or "").strip()
    if not workflow_id:
        raise ValueError(f"workflow_id is required in memory config: {memory_cfg_path}")

    memory_root = PROJECT_ROOT / "data" / "memory" / workflow_id
    return (
        memory_root / "scene",
        memory_root / "dialogues",
        memory_root / "episodes",
    )


def fetch_reference_content(config_text: str, dialogue_id: str, episode_id: str) -> dict[str, Any]:
    safe_dialogue_id = str(dialogue_id or "").strip()
    safe_episode_id = str(episode_id or "").strip()
    if not safe_dialogue_id or not safe_episode_id:
        raise ValueError("dialogue_id and episode_id are required")

    config_path = _resolve_config_path(config_text)
    cache_key = (str(config_path), safe_dialogue_id, safe_episode_id)
    with _REFERENCE_CONTENT_LOCK:
        cached = _REFERENCE_CONTENT_CACHE.get(cache_key)
    if cached is not None:
        return deepcopy(cached)

    scene_dir, dialogues_dir, episodes_dir = _resolve_reference_search_paths(config_path)
    result = workflow_search_content(
        dialogue_id=safe_dialogue_id,
        episode_id=safe_episode_id,
        scene_dir=scene_dir,
        dialogues_dir=dialogues_dir,
        episodes_dir=episodes_dir,
    )
    safe_result = deepcopy(result if isinstance(result, dict) else {"result": result})
    with _REFERENCE_CONTENT_LOCK:
        _REFERENCE_CONTENT_CACHE[cache_key] = deepcopy(safe_result)
    return safe_result


def create_handler(state: TraceRunState):
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "MemoryAgentTraceHTTP/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            logger.info("%s - %s", self.address_string(), format % args)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                data = {}
            return data if isinstance(data, dict) else {}

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
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
            if path == "/api/run":
                payload = self._read_json_body()
                config_text = str(payload.get("config", r"config\prompt\agent_sys.yaml") or "").strip()
                question = str(payload.get("question", "") or "").strip()
                thread_id = str(payload.get("thread_id", "memory-agent-1") or "").strip()
                if not question:
                    self._send_json({"error": "Question is empty."}, status=400)
                    return
                try:
                    state.begin_run(config=config_text, thread_id=thread_id, question=question)
                except RuntimeError as exc:
                    self._send_json({"error": str(exc)}, status=409)
                    return
                worker = threading.Thread(
                    target=run_agent_once,
                    args=(state, config_text, question, thread_id),
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

            if path == "/api/content-ref":
                payload = self._read_json_body()
                config_text = str(payload.get("config", state.config) or state.config or "").strip()
                dialogue_id = str(payload.get("dialogue_id", "") or "").strip()
                episode_id = str(payload.get("episode_id", "") or "").strip()
                if not dialogue_id or not episode_id:
                    self._send_json({"error": "dialogue_id and episode_id are required."}, status=400)
                    return
                try:
                    result = fetch_reference_content(config_text, dialogue_id, episode_id)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=500)
                    return
                self._send_json({"ok": True, "result": result})
                return

            self._send_json({"error": "Not found"}, status=404)

    return RequestHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local web trace viewer for MemoryAgent."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="Bind port. Default: 8765")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="If set, do not auto-open the browser.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = TraceRunState()
    handler_cls = create_handler(state)
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    url = f"http://{args.host}:{args.port}/"
    logger.info("Memory Agent Web Trace Viewer listening on %s", url)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("Failed to auto-open browser. Open %s manually.", url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down viewer...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

