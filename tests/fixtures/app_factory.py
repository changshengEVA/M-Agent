from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from m_agent.api.chat_api_web import create_app

from .runtime_fakes import FakeRuntime
from .user_access_fakes import FakeUserAccessService


def build_test_runtime(*, config_path: Path | None = None, default_thread_id: str = "demo-thread") -> FakeRuntime:
    return FakeRuntime(
        config_path=config_path or Path("tests/configs/chat_api.test.yaml"),
        default_thread_id=default_thread_id,
    )


def build_test_user_access(*, users_root: Path | None = None) -> FakeUserAccessService:
    return FakeUserAccessService(users_root=users_root or Path("tests/tmp-users"))


def build_test_app(
    *,
    auth_enabled: bool = False,
    service_runtime: FakeRuntime | None = None,
    user_access: FakeUserAccessService | None = None,
    schedule_beat_seconds: int = 60,
    schedule_busy_retry_seconds: int = 5,
) -> FastAPI:
    runtime = service_runtime or build_test_runtime()
    active_user_access = user_access
    if auth_enabled and active_user_access is None:
        active_user_access = build_test_user_access()
    if not auth_enabled:
        active_user_access = None
    return create_app(
        service_runtime=runtime,
        user_access=active_user_access,
        schedule_beat_seconds=schedule_beat_seconds,
        schedule_busy_retry_seconds=schedule_busy_retry_seconds,
    )
