#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import queue
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
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Agents.memory_agent import create_memory_agent
from utils.logging_trace import (
    FunctionColorMapper,
    FunctionTraceHandler,
    TraceEvent,
    configure_colored_logging,
)


class MemoryAgentTraceUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Memory Agent Call Trace")
        self.root.geometry("1280x760")

        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.row_index = 0
        self.color_mapper = FunctionColorMapper()
        self.known_tags: set[str] = set()

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

        ttk.Label(right, text="Result JSON").pack(anchor=tk.W)
        self.result_text = scrolledtext.ScrolledText(right, wrap=tk.WORD, height=30)
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
        for item_id in self.trace_table.get_children(""):
            self.trace_table.delete(item_id)
        self.result_text.delete("1.0", tk.END)

    def _drain_queue(self) -> None:
        while not self.event_queue.empty():
            event_type, payload = self.event_queue.get()
            if event_type == "trace":
                self._append_trace(payload)  # type: ignore[arg-type]
            elif event_type == "result":
                self.result_text.delete("1.0", tk.END)
                self.result_text.insert(tk.END, json.dumps(payload, ensure_ascii=False, indent=2))
            elif event_type == "error":
                self.result_text.delete("1.0", tk.END)
                self.result_text.insert(tk.END, str(payload))
                self.status_var.set("Failed")
            elif event_type == "done":
                if self.status_var.get() != "Failed":
                    self.status_var.set("Done")
                self.run_button.configure(state=tk.NORMAL)

        self.root.after(100, self._drain_queue)

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
