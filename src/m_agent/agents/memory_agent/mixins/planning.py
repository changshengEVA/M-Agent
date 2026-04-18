from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryAgentPlanningMixin:
    @staticmethod
    def _extract_message_text(message: Any) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
            return "\n".join(chunk for chunk in chunks if chunk)
        return str(content or "")

    @classmethod
    def _parse_json_block(cls, text: str) -> Optional[Dict[str, Any]]:
        if not isinstance(text, str) or not text.strip():
            return None
        stripped = text.strip()
        candidates = [stripped]
        matched = cls._JSON_BLOCK_PATTERN.search(stripped)
        if matched:
            candidates.append(matched.group(0))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _payload_from_response_text(self, response_text: str) -> Dict[str, Any]:
        parsed = self._parse_json_block(response_text)
        if isinstance(parsed, dict):
            return self._normalize_output(parsed)
        return self._normalize_output(
            {
                "answer": str(response_text or "").strip(),
                "gold_answer": None,
                "evidence": None,
            }
        )

    def _payload_from_model_response(self, response: Any) -> Dict[str, Any]:
        return self._payload_from_response_text(self._extract_message_text(response))

    def _promote_short_answer_to_gold(self, payload: Dict[str, Any]) -> None:
        answer_text = str(payload.get("answer", "") or "").strip()
        if (
            payload.get("gold_answer") is None
            and answer_text
            and not self._is_unanswerable_text(answer_text)
            and len(answer_text) <= 120
            and "\n" not in answer_text
        ):
            payload["gold_answer"] = answer_text
