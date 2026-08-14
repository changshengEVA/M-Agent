from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class ChatImageAttachment(BaseModel):
    upload_id: Optional[str] = None
    image_url: Optional[str] = None
    image_file: Optional[str] = None
    blip_caption: Optional[str] = None
    mime_type: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


class ChatRunCreateRequest(BaseModel):
    thread_id: Optional[str] = None
    message: Optional[str] = None
    config: Optional[str] = None
    attachments: Optional[list[ChatImageAttachment]] = None


class UserRegisterRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = "basic"
    display_name: Optional[str] = None
    assistant_name: Optional[str] = None
    persona_prompt: Optional[str] = None
    workflow_id: Optional[str] = None


class UserLoginRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None


class UserConfigPatchRequest(BaseModel):
    # A settings request must never look successful while silently dropping
    # a stale or misspelled section.  This also makes the UI/backend contract
    # drift visible to older clients instead of pretending that it applied.
    model_config = ConfigDict(extra="forbid")

    chat: Optional[Dict[str, Any]] = None
    model: Optional[Dict[str, Any]] = None


class ObservationMonitorSettingsPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: Dict[str, Any]
    expected_revision: Optional[str] = None


class ThreadMemoryModeRequest(BaseModel):
    mode: Optional[str] = None
    discard_pending: bool = False


class ThreadMemoryFlushRequest(BaseModel):
    reason: Optional[str] = None


class DialogueImportRequest(BaseModel):
    """Import on-disk dialogue JSON into the current user's ``chat-api/<user>/`` tree."""

    migrate_legacy: bool = False
    rebuild_rag: bool = False
    index_rag: bool = True
    copy_files: bool = True
    dialogue_ids: Optional[list[str]] = None


class ThreadStimulusRequest(BaseModel):
    kind: Optional[str] = "user_message"
    text: Optional[str] = None
    attachments: Optional[list[ChatImageAttachment]] = None
    priority_override: Optional[int] = None


class ScheduleCreateRequest(BaseModel):
    deferred_objective: Optional[str] = None
    # Deprecated compatibility alias. New callers should send
    # ``deferred_objective`` so its semantic role is explicit.
    text: Optional[str] = None
    due_at: Optional[str] = None
    timezone_name: Optional[str] = None
