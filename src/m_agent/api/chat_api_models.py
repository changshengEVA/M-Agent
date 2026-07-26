from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel


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
    chat: Optional[Dict[str, Any]] = None
    model: Optional[Dict[str, Any]] = None


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
    text: Optional[str] = None
    due_at: Optional[str] = None
    timezone_name: Optional[str] = None
