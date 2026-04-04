from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel


class ChatRunCreateRequest(BaseModel):
    thread_id: Optional[str] = None
    message: Optional[str] = None
    config: Optional[str] = None


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
    memory_agent: Optional[Dict[str, Any]] = None
    memory_core: Optional[Dict[str, Any]] = None


class ThreadMemoryModeRequest(BaseModel):
    mode: Optional[str] = None
    discard_pending: bool = False


class ThreadMemoryFlushRequest(BaseModel):
    reason: Optional[str] = None


class ScheduleCreateRequest(BaseModel):
    title: Optional[str] = None
    prompt: Optional[str] = None
    due_at: Optional[str] = None
    timezone_name: Optional[str] = None
    original_time_text: Optional[str] = None
    source_text: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ScheduleUpdateRequest(BaseModel):
    title: Optional[str] = None
    prompt: Optional[str] = None
    due_at: Optional[str] = None
    timezone_name: Optional[str] = None
    original_time_text: Optional[str] = None
    source_text: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
