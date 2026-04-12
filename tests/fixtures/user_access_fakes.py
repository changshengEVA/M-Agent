from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from m_agent.api.user_access import AuthenticatedUser, UserAccessError

from .runtime_fakes import FakeRuntime


@dataclass
class _StoredUser:
    username: str
    password: str
    role: str
    display_name: str
    config_path: Path
    created_at: str
    updated_at: str

    def to_authenticated_user(self) -> AuthenticatedUser:
        return AuthenticatedUser(
            username=self.username,
            role=self.role,
            config_path=self.config_path,
            created_at=self.created_at,
            updated_at=self.updated_at,
            display_name=self.display_name,
        )


class FakeUserAccessService:
    def __init__(self, *, users_root: Path) -> None:
        self.users_root = Path(users_root)
        self._users: dict[str, _StoredUser] = {}
        self._sessions: dict[str, str] = {}
        self._runtimes: dict[str, FakeRuntime] = {}

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def health_payload(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "users_count": len(self._users),
            "sessions_count": len(self._sessions),
        }

    def shutdown(self) -> None:
        return None

    def register_user(
        self,
        *,
        username: str,
        password: str,
        role: str,
        display_name: str | None = None,
        assistant_name: str | None = None,
        persona_prompt: str | None = None,
        workflow_id: str | None = None,
    ) -> Dict[str, Any]:
        del assistant_name, persona_prompt, workflow_id
        safe_username = str(username or "").strip().lower()
        safe_password = str(password or "")
        if not safe_username:
            raise UserAccessError("username is required", status_code=400)
        if len(safe_password) < 8:
            raise UserAccessError("password must be at least 8 characters", status_code=400)
        if safe_username in self._users:
            raise UserAccessError("username already exists", status_code=409)
        now = self._now_iso()
        user = _StoredUser(
            username=safe_username,
            password=safe_password,
            role=str(role or "basic").strip().lower() or "basic",
            display_name=str(display_name or safe_username).strip() or safe_username,
            config_path=(self.users_root / safe_username / "chat.yaml"),
            created_at=now,
            updated_at=now,
        )
        self._users[safe_username] = user
        self._runtimes[safe_username] = FakeRuntime(config_path=user.config_path)
        return {"user": user.to_authenticated_user().to_payload()}

    def login(self, *, username: str, password: str) -> Dict[str, Any]:
        safe_username = str(username or "").strip().lower()
        user = self._users.get(safe_username)
        if user is None or user.password != str(password or ""):
            raise UserAccessError("invalid username or password", status_code=401)
        token = f"token-{safe_username}"
        self._sessions[token] = safe_username
        auth_user = user.to_authenticated_user()
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": auth_user.to_payload(),
        }

    def authenticate(self, token: str) -> AuthenticatedUser:
        safe_token = str(token or "").strip()
        username = self._sessions.get(safe_token)
        if not username:
            raise UserAccessError("invalid or expired session token", status_code=401)
        user = self._users.get(username)
        if user is None:
            raise UserAccessError("user not found", status_code=404)
        return user.to_authenticated_user()

    def logout(self, token: str) -> None:
        self._sessions.pop(str(token or "").strip(), None)

    def get_runtime(self, *, user: AuthenticatedUser) -> FakeRuntime:
        runtime = self._runtimes.get(user.username)
        if runtime is None:
            runtime = FakeRuntime(config_path=user.config_path)
            self._runtimes[user.username] = runtime
        return runtime

    def get_user(self, *, username: str) -> AuthenticatedUser | None:
        user = self._users.get(str(username or "").strip().lower())
        return None if user is None else user.to_authenticated_user()

    def list_usernames(self) -> list[str]:
        return sorted(self._users.keys())

    def get_user_config_schema(self, *, user: AuthenticatedUser) -> Dict[str, Any]:
        return {
            "user": user.to_payload(),
            "sections": {
                "chat": {
                    "editable_fields": ["chat_assistant_name", "chat_persona_prompt"],
                    "fields": {},
                }
            },
        }

    def update_user_config(self, *, user: AuthenticatedUser, updates: Dict[str, Any]) -> Dict[str, Any]:
        stored = self._users.get(user.username)
        if stored is None:
            raise UserAccessError("user not found", status_code=404)
        stored.updated_at = self._now_iso()
        return {
            "success": True,
            "user": stored.to_authenticated_user().to_payload(),
            "applied_updates": updates,
        }
