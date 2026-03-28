#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import queue
import re
import sys
import threading
import traceback
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import scrolledtext, ttk
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"tkinter is required for this UI script: {exc}")

# Ensure project root is importable when running this file directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from m_agent.agents.memory_agent import create_memory_agent
from m_agent.utils.logging_trace import (
    FunctionColorMapper,
    FunctionTraceHandler,
    TraceEvent,
    configure_colored_logging,
)


class MemoryAgentTraceUI:
    PLAN_UPDATE_PREFIX = "PLAN UPDATE: "
    SUBQ_START_PREFIX = "SUBQ START: "
    SUBQ_DONE_PREFIX = "SUBQ DONE: "

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Memory Agent Call Trace")
        self.root.geometry("1280x760")

        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.row_index = 0
        self.color_mapper = FunctionColorMapper()
        self.known_tags: set[str] = set()
        self.live_plan: dict[str, object] = {}
        self.live_sub_questions: list[dict[str, object]] = []

        self.config_var = tk.StringVar(value=r"config\prompt\agent_sys.yaml")
        self.thread_var = tk.StringVar(value="memory-agent-1")
        self.question_var = tk.StringVar(
            value="When is Jon's group performing at a festival?"
        )
        self.status_var = tk.StringVar(value="Ready")

        self._build_layout()
        self._setup_logging()

        self.root.after(100, self._drain_queue)

    def _build_layout(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Config").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(top, textvariable=self.config_var, width=52).grid(
            row=0, column=1, padx=6, sticky=tk.EW
        )
        ttk.Label(top, text="Thread").grid(row=0, column=2, sticky=tk.W)
        ttk.Entry(top, textvariable=self.thread_var, width=20).grid(
            row=0, column=3, padx=6, sticky=tk.W
        )

        ttk.Label(top, text="Question").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(top, textvariable=self.question_var).grid(
            row=1, column=1, columnspan=3, padx=6, pady=(8, 0), sticky=tk.EW
        )

        self.run_button = ttk.Button(top, text="Run", command=self._run_query)
        self.run_button.grid(row=0, column=4, rowspan=2, padx=(8, 0), sticky=tk.NS)

        top.columnconfigure(1, weight=1)

        center = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        center.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(center)
        right = ttk.Frame(center)
        center.add(left, weight=7)
        center.add(right, weight=3)

        columns = ("step", "time", "phase", "function", "detail")
        self.trace_table = ttk.Treeview(left, columns=columns, show="headings", height=25)
        for column, width in (
            ("step", 56),
            ("time", 70),
            ("phase", 80),
            ("function", 220),
            ("detail", 580),
        ):
            self.trace_table.heading(column, text=column.upper())
            self.trace_table.column(column, width=width, anchor=tk.W, stretch=column == "detail")

        trace_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.trace_table.yview)
        self.trace_table.configure(yscrollcommand=trace_scroll.set)
        self.trace_table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        trace_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        right_pane = ttk.Panedwindow(right, orient=tk.VERTICAL)
        right_pane.pack(fill=tk.BOTH, expand=True)

        plan_frame = ttk.Frame(right, padding=(0, 0, 0, 6))
        result_frame = ttk.Frame(right)
        right_pane.add(plan_frame, weight=3)
        right_pane.add(result_frame, weight=5)

        ttk.Label(plan_frame, text="Plan / Sub-questions").pack(anchor=tk.W)
        self.plan_text = scrolledtext.ScrolledText(plan_frame, wrap=tk.WORD, height=12)
        self.plan_text.pack(fill=tk.BOTH, expand=True)

        ttk.Label(result_frame, text="Result JSON").pack(anchor=tk.W)
        self.result_text = scrolledtext.ScrolledText(result_frame, wrap=tk.WORD, height=18)
        self.result_text.pack(fill=tk.BOTH, expand=True)

        status = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W, padding=8)
        status.pack(fill=tk.X)

        self.trace_table.tag_configure("INFO", foreground="#334155")
        self.trace_table.tag_configure("WARNING", foreground="#b45309")
        self.trace_table.tag_configure("ERROR", foreground="#b91c1c")

    def _setup_logging(self) -> None:
        configure_colored_logging(level=logging.INFO)
        logging.getLogger("memory.memory_core").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)

        self.trace_handler = FunctionTraceHandler(
            callback=self._enqueue_trace,
            include_non_api=True,
        )
        trace_logger = logging.getLogger("Agents.memory_agent")
        trace_logger.addHandler(self.trace_handler)
        trace_logger.setLevel(logging.INFO)

    def _enqueue_trace(self, event: TraceEvent) -> None:
        self.event_queue.put(("trace", event))

    def _run_query(self) -> None:
        question = self.question_var.get().strip()
        if not question:
            self.status_var.set("Question is empty")
            return

        self._reset_view()
        self.run_button.configure(state=tk.DISABLED)
        self.status_var.set("Running...")

        config_text = self.config_var.get().strip()
        thread_text = self.thread_var.get().strip()
        thread = threading.Thread(
            target=self._run_agent,
            args=(config_text, question, thread_text),
            daemon=True,
        )
        thread.start()

    def _run_agent(self, config_text: str, question: str, thread_id: str) -> None:
        try:
            config_path = Path(config_text)
            if not config_path.is_absolute():
                config_path = PROJECT_ROOT / config_path
            agent = create_memory_agent(config_path=config_path)
            result = agent.ask(question=question, thread_id=thread_id or None)
            self.event_queue.put(("result", result))
        except Exception:
            self.event_queue.put(("error", traceback.format_exc()))
        finally:
            self.event_queue.put(("done", None))

    def _reset_view(self) -> None:
        self.row_index = 0
        self.live_plan = {}
        self.live_sub_questions = []
        for item_id in self.trace_table.get_children(""):
            self.trace_table.delete(item_id)
        self.plan_text.delete("1.0", tk.END)
        self.result_text.delete("1.0", tk.END)

    def _drain_queue(self) -> None:
        while not self.event_queue.empty():
            event_type, payload = self.event_queue.get()
            if event_type == "trace":
                self._append_trace(payload)  # type: ignore[arg-type]
                self._update_live_plan_from_trace(payload)  # type: ignore[arg-type]
            elif event_type == "result":
                self._render_plan(payload)
                self.result_text.delete("1.0", tk.END)
                self.result_text.insert(tk.END, json.dumps(payload, ensure_ascii=False, indent=2))
            elif event_type == "error":
                self.plan_text.delete("1.0", tk.END)
                self.result_text.delete("1.0", tk.END)
                self.result_text.insert(tk.END, str(payload))
                self.status_var.set("Failed")
            elif event_type == "done":
                if self.status_var.get() != "Failed":
                    self.status_var.set("Done")
                self.run_button.configure(state=tk.NORMAL)

        self.root.after(100, self._drain_queue)

    def _render_plan(self, payload: object) -> None:
        if not isinstance(payload, dict):
            self._render_live_plan_text()
            return

        question_plan = payload.get("question_plan")
        sub_question_results = payload.get("sub_question_results")
        if isinstance(question_plan, dict) and question_plan:
            self.live_plan = dict(question_plan)
        if isinstance(sub_question_results, list):
            self.live_sub_questions = [
                dict(item) for item in sub_question_results if isinstance(item, dict)
            ]
        self._render_live_plan_text()

    def _update_live_plan_from_trace(self, event: TraceEvent) -> None:
        if not isinstance(event, TraceEvent):
            return

        raw_message = event.raw_message or ""
        if raw_message.startswith(self.PLAN_UPDATE_PREFIX):
            payload = raw_message[len(self.PLAN_UPDATE_PREFIX) :].strip()
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                self.live_plan = parsed
                self._initialize_live_sub_questions()
                self._render_live_plan_text()
            return

        if raw_message.startswith(self.SUBQ_START_PREFIX):
            payload = raw_message[len(self.SUBQ_START_PREFIX) :].strip()
            self._apply_sub_question_update(payload, default_status="in_progress")
            return

        if raw_message.startswith(self.SUBQ_DONE_PREFIX):
            payload = raw_message[len(self.SUBQ_DONE_PREFIX) :].strip()
            self._apply_sub_question_update(payload, default_status="completed")
            return

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

    def _apply_sub_question_update(self, payload: str, default_status: str) -> None:
        try:
            parsed = json.loads(payload)
        except Exception:
            return
        if not isinstance(parsed, dict):
            return

        try:
            index_int = int(parsed.get("index"))
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
        if parsed.get("question") is not None:
            item["question"] = str(parsed.get("question") or "")
        item["status"] = str(parsed.get("status") or default_status)
        if parsed.get("answer") is not None:
            item["answer"] = str(parsed.get("answer") or "")
        self._render_live_plan_text()

    def _render_live_plan_text(self) -> None:
        self.plan_text.delete("1.0", tk.END)

        lines: list[str] = []
        question_plan = self.live_plan if isinstance(self.live_plan, dict) else {}

        goal = str(question_plan.get("goal", "") or "").strip()
        if goal:
            lines.append(f"Goal: {goal}")

        if self.live_sub_questions:
            if lines:
                lines.append("")
            lines.append("Sub-questions:")
            for item in self.live_sub_questions:
                index = int(item.get("index", 0) or 0)
                question = str(item.get("question", "") or "").strip()
                status = str(item.get("status", "pending") or "pending").strip().lower()
                answer = str(item.get("answer", "") or "").strip()

                if status == "completed":
                    status_label = "done"
                elif status == "in_progress":
                    status_label = "running"
                elif status == "failed":
                    status_label = "failed"
                else:
                    status_label = "pending"

                lines.append(f"{index}. [{status_label}] {question}")
                if answer:
                    answer_text = re.sub(r"\s+", " ", answer)
                    lines.append(f"   Answer: {answer_text}")

        if not lines:
            lines.append("Waiting for decomposition...")

        self.plan_text.insert(tk.END, "\n".join(lines))

    def _append_trace(self, event: TraceEvent) -> None:
        self.row_index += 1

        if event.function_name:
            tag = f"func::{event.function_name}"
            if tag not in self.known_tags:
                self.trace_table.tag_configure(
                    tag,
                    foreground=self.color_mapper.ui_color_for(event.function_name),
                )
                self.known_tags.add(tag)
            phase = event.phase or event.level_name
            function_name = event.function_name
            detail = event.detail or event.raw_message
        else:
            tag = event.level_name if event.level_name in {"INFO", "WARNING", "ERROR"} else "INFO"
            phase = event.level_name
            function_name = event.logger_name
            detail = event.raw_message

        row_id = self.trace_table.insert(
            "",
            tk.END,
            values=(
                self.row_index,
                event.timestamp,
                phase,
                function_name,
                detail,
            ),
            tags=(tag,),
        )
        self.trace_table.see(row_id)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = MemoryAgentTraceUI()
    app.run()


if __name__ == "__main__":
    main()

