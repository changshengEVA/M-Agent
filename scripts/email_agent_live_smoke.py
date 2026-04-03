#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for p in (PROJECT_ROOT, SRC_ROOT):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from m_agent.agents import EmailAgent  # noqa: E402


def _pretty(title: str, payload: Dict[str, Any]) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Live smoke test for EmailAgent (Gmail OAuth).")
    parser.add_argument("--config", default="config/agents/email/gmail_email_agent.yaml", help="EmailAgent YAML path.")
    parser.add_argument(
        "--instruction",
        "--query",
        dest="instruction",
        default="\u5e2e\u6211\u770b\u770b\u6709\u6ca1\u6709\u5b9e\u4e60\u62db\u8058\u76f8\u5173\u90ae\u4ef6",
        help="Instruction for ask().",
    )
    parser.add_argument("--mail-scope", default="all", choices=["unread", "all"], help="Search scope for ask().")
    parser.add_argument("--debug", action="store_true", help="Include trace in ask() response.")
    parser.add_argument("--send-to", default="", help="Recipient for send() test.")
    parser.add_argument("--send-subject", default="EmailAgent send test", help="Subject for send().")
    parser.add_argument("--send-content", default="This is a direct send test from EmailAgent.", help="Body text for send().")
    args = parser.parse_args()

    agent = EmailAgent(config_path=args.config)
    tool_names = [tool.name for tool in agent.tools]
    print("Loaded tools:", tool_names)
    tool_map = {tool.name: tool for tool in agent.tools}

    missing = sorted({"ask", "send"} - set(tool_map))
    if missing:
        raise RuntimeError(f"Missing expected tools: {missing}")

    ask_result = tool_map["ask"].invoke(
        {"instruction": args.instruction, "mail_scope": args.mail_scope, "debug": bool(args.debug)}
    )
    _pretty("ask", ask_result)

    if not args.send_to.strip():
        print("\nSkip send: provide --send-to to test sending.")
        return

    send_result = tool_map["send"].invoke(
        {
            "content": args.send_content,
            "to": args.send_to.strip(),
            "subject": args.send_subject,
        }
    )
    _pretty("send", send_result)


if __name__ == "__main__":
    main()
