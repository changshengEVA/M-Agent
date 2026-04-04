from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml

from m_agent.config_paths import (
    CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
    DEFAULT_EMAIL_AGENT_CONFIG_PATH,
    resolve_related_config_path,
)
from m_agent.paths import PROJECT_ROOT


DEFAULT_USERS_ROOT_DIR = PROJECT_ROOT / "config" / "users"
DEFAULT_USERS_DB_PATH = DEFAULT_USERS_ROOT_DIR / "users.json"

_USER_CHAT_CONFIG_NAME = "chat.yaml"
_USER_MEMORY_AGENT_CONFIG_NAME = "memory_agent.params.yaml"
_USER_MEMORY_CORE_CONFIG_NAME = "memory_core.params.yaml"
_USER_CHAT_RUNTIME_NAME = "chat_runtime.yaml"

_PASSWORD_PBKDF2_ITERATIONS = 150_000
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")

_BASIC_EDITABLE_FIELDS: Dict[str, set[str]] = {
    "chat": {"chat_assistant_name", "chat_persona_prompt"},
    "memory_agent": set(),
    "memory_core": set(),
}
_ADVANCED_EXTRA_FIELDS: Dict[str, set[str]] = {
    "chat": {"chat_user_name", "persist_memory", "enabled_tools", "tool_defaults", "thread_id"},
    "memory_agent": {
        "model_name",
        "agent_temperature",
        "recursion_limit",
        "retry_recursion_limit",
        "detail_search_defaults",
        "network_retry_attempts",
        "network_retry_backoff_seconds",
        "network_retry_backoff_multiplier",
        "network_retry_max_backoff_seconds",
    },
    "memory_core": {
        "workflow_id",
        "memory_owner_name",
        "memory_similarity_threshold",
        "memory_top_k",
        "memory_use_threshold",
        "embed_provider",
    },
}
_ADVANCED_EDITABLE_FIELDS: Dict[str, set[str]] = {
    section: set(_BASIC_EDITABLE_FIELDS.get(section, set())) | set(_ADVANCED_EXTRA_FIELDS.get(section, set()))
    for section in ("chat", "memory_agent", "memory_core")
}
_CONFIG_FIELD_SCHEMAS: Dict[str, Dict[str, Dict[str, str]]] = {
    "chat": {
        "chat_assistant_name": {
            "type": "string",
            "description": "Assistant display name used in chat responses.",
        },
        "chat_persona_prompt": {
            "type": "string",
            "description": "System persona prompt for the chat assistant.",
        },
        "chat_user_name": {
            "type": "string",
            "description": "Display name of the current user in chat context.",
        },
        "persist_memory": {
            "type": "boolean",
            "description": "Whether captured memory can be persisted.",
        },
        "enabled_tools": {
            "type": "array[string]",
            "description": "Tool IDs enabled for the chat controller.",
        },
        "tool_defaults": {
            "type": "object",
            "description": "Default arguments keyed by tool ID.",
        },
        "thread_id": {
            "type": "string",
            "description": "Default thread id for chat runs.",
        },
    },
    "memory_agent": {
        "model_name": {
            "type": "string",
            "description": "Primary model name used by memory agent.",
        },
        "agent_temperature": {
            "type": "number",
            "description": "Sampling temperature for memory-agent generation.",
        },
        "recursion_limit": {
            "type": "integer",
            "description": "Maximum recursion depth for tool/agent planning.",
        },
        "retry_recursion_limit": {
            "type": "integer",
            "description": "Retry recursion limit when network-bound tool calls fail.",
        },
        "detail_search_defaults": {
            "type": "object",
            "description": "Default detail-search parameters used by memory tools.",
        },
        "network_retry_attempts": {
            "type": "integer",
            "description": "Retry attempts for network/API operations.",
        },
        "network_retry_backoff_seconds": {
            "type": "number",
            "description": "Initial backoff seconds for network retries.",
        },
        "network_retry_backoff_multiplier": {
            "type": "number",
            "description": "Exponential multiplier for retry backoff.",
        },
        "network_retry_max_backoff_seconds": {
            "type": "number",
            "description": "Maximum backoff cap for network retries.",
        },
    },
    "memory_core": {
        "workflow_id": {
            "type": "string",
            "description": "Workflow namespace used to isolate memory data.",
        },
        "memory_owner_name": {
            "type": "string",
            "description": "Owner name shown in memory summaries.",
        },
        "memory_similarity_threshold": {
            "type": "number",
            "description": "Similarity threshold for recall matching.",
        },
        "memory_top_k": {
            "type": "integer",
            "description": "Top-k candidate count for memory recall.",
        },
        "memory_use_threshold": {
            "type": "boolean",
            "description": "Whether similarity threshold filtering is enabled.",
        },
        "embed_provider": {
            "type": "string",
            "description": "Embedding provider used by memory core.",
        },
    },
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat().replace("+00:00", "Z")


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_slug(text: str, *, fallback: str = "user") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_.")
    return slug[:48] or fallback


def _normalize_username(value: Any) -> str:
    username = str(value or "").strip().lower()
    if not _USERNAME_PATTERN.fullmatch(username):
        raise UserAccessError(
            "username must be 3-32 chars and only contain letters, digits, dot, underscore, or dash",
            status_code=400,
        )
    return username


def _normalize_role(value: Any) -> str:
    role = str(value or "basic").strip().lower()
    if role not in {"basic", "advanced"}:
        raise UserAccessError("role must be either 'basic' or 'advanced'", status_code=400)
    return role


def _validate_password(value: Any) -> str:
    password = str(value or "")
    if len(password) < 8:
        raise UserAccessError("password must be at least 8 characters", status_code=400)
    return password


def _hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PASSWORD_PBKDF2_ITERATIONS,
    )
    return (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_password(*, password: str, salt_b64: str, digest_b64: str) -> bool:
    try:
        salt = base64.b64decode(str(salt_b64 or "").encode("ascii"))
        expected = base64.b64decode(str(digest_b64 or "").encode("ascii"))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PASSWORD_PBKDF2_ITERATIONS,
    )
    return hmac.compare_digest(actual, expected)


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    if not isinstance(payload, dict):
        raise UserAccessError(f"config must be a mapping: {path}", status_code=500)
    return payload


def _write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def _copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        raise UserAccessError(f"runtime prompt config not found: {src}", status_code=500)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _empty_users_payload() -> Dict[str, Any]:
    return {
        "version": 1,
        "users": {},
    }


class UserAccessError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    role: str
    config_path: Path
    created_at: str
    updated_at: str
    display_name: str

    @property
    def editable_fields(self) -> Dict[str, list[str]]:
        allowed = _BASIC_EDITABLE_FIELDS if self.role == "basic" else _ADVANCED_EDITABLE_FIELDS
        return {
            section: sorted(list(fields))
            for section, fields in allowed.items()
        }

    def to_payload(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "config_path": str(self.config_path),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "editable_fields": self.editable_fields,
        }


class UserAccountStore:
    def __init__(
        self,
        *,
        base_chat_config_path: Path,
        users_db_path: Path | None = None,
        users_root_dir: Path | None = None,
    ) -> None:
        self.base_chat_config_path = Path(base_chat_config_path).resolve()
        self.users_root_dir = Path(users_root_dir or DEFAULT_USERS_ROOT_DIR).resolve()
        self.users_db_path = Path(users_db_path or DEFAULT_USERS_DB_PATH).resolve()
        self._lock = threading.Lock()

    def _load_users_payload(self) -> Dict[str, Any]:
        if not self.users_db_path.exists():
            return _empty_users_payload()
        try:
            with open(self.users_db_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            raise UserAccessError(f"failed to read users db: {exc}", status_code=500) from exc
        if not isinstance(payload, dict):
            raise UserAccessError("users db must be a JSON object", status_code=500)
        users = payload.get("users")
        if not isinstance(users, dict):
            payload["users"] = {}
        payload.setdefault("version", 1)
        return payload

    def _save_users_payload(self, payload: Dict[str, Any]) -> None:
        self.users_db_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.users_db_path.with_suffix(self.users_db_path.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        temp_path.replace(self.users_db_path)

    @staticmethod
    def _user_record(users_payload: Dict[str, Any], username: str) -> Optional[Dict[str, Any]]:
        users = users_payload.get("users")
        if not isinstance(users, dict):
            return None
        record = users.get(username)
        if not isinstance(record, dict):
            return None
        return record

    def _resolve_user_config_path(self, record: Dict[str, Any]) -> Path:
        raw_path = str(record.get("config_path", "") or "").strip()
        if not raw_path:
            raise UserAccessError("user config_path is missing", status_code=500)
        path = Path(raw_path)
        if path.is_absolute():
            return path.resolve()
        return (PROJECT_ROOT / path).resolve()

    def _to_authenticated_user(self, *, username: str, record: Dict[str, Any]) -> AuthenticatedUser:
        role = _normalize_role(record.get("role", "basic"))
        config_path = self._resolve_user_config_path(record)
        display_name = str(record.get("display_name", "") or "").strip() or username
        created_at = str(record.get("created_at", "") or "").strip() or _now_iso()
        updated_at = str(record.get("updated_at", "") or "").strip() or created_at
        return AuthenticatedUser(
            username=username,
            role=role,
            config_path=config_path,
            created_at=created_at,
            updated_at=updated_at,
            display_name=display_name,
        )

    def _resolve_config_chain(self, chat_config_path: Path) -> tuple[Path, Path, Path]:
        chat_config = _load_yaml(chat_config_path)
        memory_agent_path = resolve_related_config_path(
            chat_config_path,
            chat_config.get("memory_agent_config_path"),
        )
        memory_agent_config = _load_yaml(memory_agent_path)
        memory_core_path = resolve_related_config_path(
            memory_agent_path,
            memory_agent_config.get("memory_core_config_path"),
        )
        return chat_config_path, memory_agent_path, memory_core_path

    @staticmethod
    def _resolve_runtime_prompt_path(
        config_path: Path,
        config_payload: Dict[str, Any],
        *,
        default_path: Path,
    ) -> Path:
        return resolve_related_config_path(
            config_path,
            config_payload.get("runtime_prompt_config_path"),
            default_path=default_path,
        )

    @staticmethod
    def _relative_or_absolute_path(target_path: Path, *, start_dir: Path) -> str:
        try:
            return Path(os.path.relpath(target_path, start=start_dir)).as_posix()
        except ValueError:
            # Different Windows drive letters (e.g. temp dir on C:, repo on F:).
            return str(target_path.resolve())

    @staticmethod
    def _merge_missing_mappings(target: Dict[str, Any], defaults: Dict[str, Any]) -> bool:
        changed = False
        for key, default_value in defaults.items():
            if key not in target:
                target[key] = deepcopy(default_value)
                changed = True
                continue
            existing_value = target.get(key)
            if isinstance(existing_value, dict) and isinstance(default_value, dict):
                if UserAccountStore._merge_missing_mappings(existing_value, default_value):
                    changed = True
        return changed

    def _sync_chat_tool_settings(
        self,
        *,
        chat_config_path: Path,
        user_chat_config: Dict[str, Any],
        base_chat_config: Dict[str, Any],
    ) -> bool:
        changed = False

        if self._merge_missing_mappings(user_chat_config, base_chat_config):
            changed = True

        user_email_raw = str(user_chat_config.get("email_agent_config_path", "") or "").strip()
        user_email_path = resolve_related_config_path(
            chat_config_path,
            user_email_raw,
            default_path=DEFAULT_EMAIL_AGENT_CONFIG_PATH,
        )
        if (not user_email_raw) or (not user_email_path.exists()):
            base_email_path = resolve_related_config_path(
                self.base_chat_config_path,
                base_chat_config.get("email_agent_config_path"),
                default_path=DEFAULT_EMAIL_AGENT_CONFIG_PATH,
            )
            normalized_email_path = self._relative_or_absolute_path(
                base_email_path,
                start_dir=chat_config_path.parent,
            )
            if user_chat_config.get("email_agent_config_path") != normalized_email_path:
                user_chat_config["email_agent_config_path"] = normalized_email_path
                changed = True

        return changed

    def _sync_runtime_tool_settings(
        self,
        *,
        user_runtime_config: Dict[str, Any],
        base_runtime_config: Dict[str, Any],
    ) -> bool:
        changed = False

        base_controller = base_runtime_config.get("chat_controller")
        if not isinstance(base_controller, dict):
            return changed

        user_controller = user_runtime_config.get("chat_controller")
        if not isinstance(user_controller, dict):
            user_controller = {}
            user_runtime_config["chat_controller"] = user_controller
            changed = True

        base_tools = base_controller.get("tools")
        if not isinstance(base_tools, dict):
            return changed

        user_tools = user_controller.get("tools")
        if not isinstance(user_tools, dict):
            user_tools = {}
            user_controller["tools"] = user_tools
            changed = True

        if self._merge_missing_mappings(user_tools, base_tools):
            changed = True
        return changed

    def _sync_user_tool_related_configs(self, *, record: Dict[str, Any]) -> bool:
        chat_config_path = self._resolve_user_config_path(record)
        user_chat_config = _load_yaml(chat_config_path)
        base_chat_config = _load_yaml(self.base_chat_config_path)

        changed = False
        if self._sync_chat_tool_settings(
            chat_config_path=chat_config_path,
            user_chat_config=user_chat_config,
            base_chat_config=base_chat_config,
        ):
            _write_yaml(chat_config_path, user_chat_config)
            changed = True

        base_runtime_path = self._resolve_runtime_prompt_path(
            self.base_chat_config_path,
            base_chat_config,
            default_path=CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
        )
        user_runtime_path = self._resolve_runtime_prompt_path(
            chat_config_path,
            user_chat_config,
            default_path=CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
        )
        if not user_runtime_path.exists():
            _copy_file(base_runtime_path, user_runtime_path)
            changed = True
        else:
            base_runtime_config = _load_yaml(base_runtime_path)
            user_runtime_config = _load_yaml(user_runtime_path)
            if self._sync_runtime_tool_settings(
                user_runtime_config=user_runtime_config,
                base_runtime_config=base_runtime_config,
            ):
                _write_yaml(user_runtime_path, user_runtime_config)
                changed = True

        if changed:
            record["updated_at"] = _now_iso()
        return changed

    def _scaffold_user_configs(
        self,
        *,
        username: str,
        display_name: str,
        assistant_name: str,
        persona_prompt: Optional[str],
        workflow_id: Optional[str],
    ) -> Path:
        chat_template_path, memory_agent_template_path, memory_core_template_path = self._resolve_config_chain(
            self.base_chat_config_path
        )
        chat_config = _load_yaml(chat_template_path)
        memory_agent_config = _load_yaml(memory_agent_template_path)
        memory_core_config = _load_yaml(memory_core_template_path)
        chat_runtime_template_path = self._resolve_runtime_prompt_path(
            chat_template_path,
            chat_config,
            default_path=CHAT_CONTROLLER_RUNTIME_PROMPT_CONFIG_PATH,
        )

        user_dir = self.users_root_dir / _safe_slug(username)
        if user_dir.exists():
            raise UserAccessError(f"user config directory already exists: {user_dir}", status_code=409)
        user_dir.mkdir(parents=True, exist_ok=False)

        memory_agent_base_config_path = self._relative_or_absolute_path(
            memory_agent_template_path,
            start_dir=user_dir,
        )
        email_agent_template_path = resolve_related_config_path(
            chat_template_path,
            chat_config.get("email_agent_config_path"),
            default_path=DEFAULT_EMAIL_AGENT_CONFIG_PATH,
        )

        chat_config["memory_agent_config_path"] = f"./{_USER_MEMORY_AGENT_CONFIG_NAME}"
        chat_config["runtime_prompt_config_path"] = f"./runtime/{_USER_CHAT_RUNTIME_NAME}"
        chat_config["email_agent_config_path"] = self._relative_or_absolute_path(
            email_agent_template_path,
            start_dir=user_dir,
        )
        chat_config["thread_id"] = f"{_safe_slug(username, fallback='user')}-thread"
        chat_config["chat_user_name"] = display_name
        chat_config["chat_assistant_name"] = assistant_name
        if isinstance(persona_prompt, str) and persona_prompt.strip():
            chat_config["chat_persona_prompt"] = persona_prompt.strip()

        memory_agent_config["base_config_path"] = memory_agent_base_config_path
        memory_agent_config["memory_core_config_path"] = f"./{_USER_MEMORY_CORE_CONFIG_NAME}"
        memory_agent_config["thread_id"] = f"{_safe_slug(username, fallback='user')}-memory-agent"
        memory_agent_config.pop("planner_prompt", None)
        memory_agent_config.pop("system_prompt", None)
        memory_agent_config.pop("runtime_prompt_config_path", None)

        default_workflow_id = f"user_{_safe_slug(username, fallback='user')}"
        memory_core_config["workflow_id"] = str(workflow_id or default_workflow_id)
        memory_core_config.pop("runtime_prompt_config_path", None)
        if assistant_name.strip():
            memory_core_config["memory_owner_name"] = assistant_name.strip()

        chat_user_config_path = user_dir / _USER_CHAT_CONFIG_NAME
        memory_agent_user_config_path = user_dir / _USER_MEMORY_AGENT_CONFIG_NAME
        memory_core_user_config_path = user_dir / _USER_MEMORY_CORE_CONFIG_NAME

        _write_yaml(chat_user_config_path, chat_config)
        _write_yaml(memory_agent_user_config_path, memory_agent_config)
        _write_yaml(memory_core_user_config_path, memory_core_config)
        _copy_file(
            chat_runtime_template_path,
            user_dir / "runtime" / _USER_CHAT_RUNTIME_NAME,
        )
        return chat_user_config_path

    def register_user(
        self,
        *,
        username: str,
        password: str,
        role: str = "basic",
        display_name: Optional[str] = None,
        assistant_name: Optional[str] = None,
        persona_prompt: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> AuthenticatedUser:
        normalized_username = _normalize_username(username)
        safe_password = _validate_password(password)
        normalized_role = _normalize_role(role)
        normalized_display_name = str(display_name or normalized_username).strip() or normalized_username
        normalized_assistant_name = str(assistant_name or "Memory Assistant").strip() or "Memory Assistant"
        normalized_persona_prompt = str(persona_prompt).strip() if persona_prompt is not None else None
        normalized_workflow_id = str(workflow_id).strip() if workflow_id is not None else None

        with self._lock:
            users_payload = self._load_users_payload()
            if self._user_record(users_payload, normalized_username) is not None:
                raise UserAccessError(f"user already exists: {normalized_username}", status_code=409)

            chat_user_config_path = self._scaffold_user_configs(
                username=normalized_username,
                display_name=normalized_display_name,
                assistant_name=normalized_assistant_name,
                persona_prompt=normalized_persona_prompt,
                workflow_id=normalized_workflow_id,
            )
            salt_b64, digest_b64 = _hash_password(safe_password)
            now = _now_iso()
            try:
                stored_config_path = chat_user_config_path.relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                stored_config_path = str(chat_user_config_path.resolve())
            users = users_payload.setdefault("users", {})
            users[normalized_username] = {
                "username": normalized_username,
                "display_name": normalized_display_name,
                "role": normalized_role,
                "password_salt": salt_b64,
                "password_hash": digest_b64,
                "password_kdf": "pbkdf2_sha256",
                "password_iterations": _PASSWORD_PBKDF2_ITERATIONS,
                "config_path": stored_config_path,
                "created_at": now,
                "updated_at": now,
            }
            self._save_users_payload(users_payload)
            return self._to_authenticated_user(
                username=normalized_username,
                record=users[normalized_username],
            )

    def verify_credentials(self, *, username: str, password: str) -> AuthenticatedUser:
        normalized_username = _normalize_username(username)
        safe_password = _validate_password(password)
        with self._lock:
            users_payload = self._load_users_payload()
            record = self._user_record(users_payload, normalized_username)
            if record is None:
                raise UserAccessError("invalid username or password", status_code=401)
            if not _verify_password(
                password=safe_password,
                salt_b64=str(record.get("password_salt", "")),
                digest_b64=str(record.get("password_hash", "")),
            ):
                raise UserAccessError("invalid username or password", status_code=401)
            if self._sync_user_tool_related_configs(record=record):
                self._save_users_payload(users_payload)
            return self._to_authenticated_user(username=normalized_username, record=record)

    def get_user(self, *, username: str) -> Optional[AuthenticatedUser]:
        normalized_username = _normalize_username(username)
        with self._lock:
            users_payload = self._load_users_payload()
            record = self._user_record(users_payload, normalized_username)
            if record is None:
                return None
            return self._to_authenticated_user(username=normalized_username, record=record)

    def get_user_config_schema(self, *, username: str) -> Dict[str, Any]:
        normalized_username = _normalize_username(username)
        with self._lock:
            users_payload = self._load_users_payload()
            record = self._user_record(users_payload, normalized_username)
            if record is None:
                raise UserAccessError("user not found", status_code=404)

            user_role = _normalize_role(record.get("role", "basic"))
            allowed_fields = _BASIC_EDITABLE_FIELDS if user_role == "basic" else _ADVANCED_EDITABLE_FIELDS
            known_fields = _ADVANCED_EDITABLE_FIELDS

            chat_path = self._resolve_user_config_path(record)
            _, memory_agent_path, memory_core_path = self._resolve_config_chain(chat_path)
            section_targets: Dict[str, Dict[str, Any]] = {
                "chat": _load_yaml(chat_path),
                "memory_agent": _load_yaml(memory_agent_path),
                "memory_core": _load_yaml(memory_core_path),
            }

            sections_payload: Dict[str, Dict[str, Any]] = {}
            for section in ("chat", "memory_agent", "memory_core"):
                target = section_targets[section]
                editable_keys = sorted(list(allowed_fields[section]))
                section_fields: Dict[str, Dict[str, Any]] = {}
                for key in sorted(list(known_fields[section])):
                    schema_meta = _CONFIG_FIELD_SCHEMAS.get(section, {}).get(key, {})
                    section_fields[key] = {
                        "type": str(schema_meta.get("type", "any")),
                        "description": str(schema_meta.get("description", "")).strip(),
                        "editable": key in allowed_fields[section],
                        "present": key in target,
                        "current_value": deepcopy(target.get(key)),
                    }

                patch_example: Dict[str, Any] = {}
                for key in editable_keys:
                    if key in target:
                        patch_example[key] = deepcopy(target.get(key))

                sections_payload[section] = {
                    "editable_fields": editable_keys,
                    "fields": section_fields,
                    "patch_example": patch_example,
                }

            return {
                "user": {
                    "username": normalized_username,
                    "role": user_role,
                    "config_path": str(chat_path),
                },
                "sections": sections_payload,
            }

    def user_count(self) -> int:
        with self._lock:
            users_payload = self._load_users_payload()
            users = users_payload.get("users")
            return len(users) if isinstance(users, dict) else 0

    @staticmethod
    def _resolve_section_updates(raw_updates: Dict[str, Any], section: str) -> Dict[str, Any]:
        value = raw_updates.get(section)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise UserAccessError(f"'{section}' must be an object", status_code=400)
        return dict(value)

    def update_user_config(
        self,
        *,
        username: str,
        updates: Dict[str, Any],
    ) -> AuthenticatedUser:
        normalized_username = _normalize_username(username)
        if not isinstance(updates, dict):
            raise UserAccessError("updates must be an object", status_code=400)

        with self._lock:
            users_payload = self._load_users_payload()
            record = self._user_record(users_payload, normalized_username)
            if record is None:
                raise UserAccessError("user not found", status_code=404)

            user_role = _normalize_role(record.get("role", "basic"))
            allowed_fields = _BASIC_EDITABLE_FIELDS if user_role == "basic" else _ADVANCED_EDITABLE_FIELDS
            known_fields = _ADVANCED_EDITABLE_FIELDS

            chat_updates = self._resolve_section_updates(updates, "chat")
            memory_agent_updates = self._resolve_section_updates(updates, "memory_agent")
            memory_core_updates = self._resolve_section_updates(updates, "memory_core")
            if not chat_updates and not memory_agent_updates and not memory_core_updates:
                raise UserAccessError("no config fields provided", status_code=400)

            chat_path = self._resolve_user_config_path(record)
            _, memory_agent_path, memory_core_path = self._resolve_config_chain(chat_path)
            chat_config = _load_yaml(chat_path)
            memory_agent_config = _load_yaml(memory_agent_path)
            memory_core_config = _load_yaml(memory_core_path)

            changed_sections: set[str] = set()
            section_targets = {
                "chat": chat_config,
                "memory_agent": memory_agent_config,
                "memory_core": memory_core_config,
            }
            section_updates = {
                "chat": chat_updates,
                "memory_agent": memory_agent_updates,
                "memory_core": memory_core_updates,
            }
            for section in ("chat", "memory_agent", "memory_core"):
                target = section_targets[section]
                updates_for_section = section_updates[section]
                for key, value in updates_for_section.items():
                    key_text = str(key or "").strip()
                    if not key_text:
                        raise UserAccessError(f"invalid empty key in '{section}'", status_code=400)
                    if key_text not in known_fields[section]:
                        raise UserAccessError(f"unsupported config key '{section}.{key_text}'", status_code=400)
                    if key_text not in allowed_fields[section]:
                        raise UserAccessError(
                            f"role '{user_role}' cannot edit '{section}.{key_text}'",
                            status_code=403,
                        )
                    target[key_text] = value
                    changed_sections.add(section)

            if not changed_sections:
                raise UserAccessError("no effective config change", status_code=400)

            if "chat" in changed_sections:
                _write_yaml(chat_path, chat_config)
            if "memory_agent" in changed_sections:
                _write_yaml(memory_agent_path, memory_agent_config)
            if "memory_core" in changed_sections:
                _write_yaml(memory_core_path, memory_core_config)

            record["updated_at"] = _now_iso()
            self._save_users_payload(users_payload)
            return self._to_authenticated_user(username=normalized_username, record=record)


class UserRuntimePool:
    def __init__(self, *, runtime_factory: Callable[[AuthenticatedUser], Any]) -> None:
        self._runtime_factory = runtime_factory
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}

    def get_runtime(self, user: AuthenticatedUser) -> Any:
        with self._lock:
            entry = self._entries.get(user.username)
            if (
                isinstance(entry, dict)
                and str(entry.get("updated_at", "")) == user.updated_at
                and Path(entry.get("config_path")).resolve() == user.config_path.resolve()
            ):
                return entry.get("runtime")

            if isinstance(entry, dict):
                runtime_to_close = entry.get("runtime")
                if runtime_to_close is not None and hasattr(runtime_to_close, "shutdown"):
                    runtime_to_close.shutdown()

            runtime = self._runtime_factory(user)
            self._entries[user.username] = {
                "runtime": runtime,
                "config_path": str(user.config_path),
                "updated_at": user.updated_at,
            }
            return runtime

    def invalidate(self, username: str) -> None:
        key = _normalize_username(username)
        with self._lock:
            entry = self._entries.pop(key, None)
            if isinstance(entry, dict):
                runtime_to_close = entry.get("runtime")
                if runtime_to_close is not None and hasattr(runtime_to_close, "shutdown"):
                    runtime_to_close.shutdown()

    def runtime_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def shutdown(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            runtime_to_close = entry.get("runtime") if isinstance(entry, dict) else None
            if runtime_to_close is not None and hasattr(runtime_to_close, "shutdown"):
                runtime_to_close.shutdown()


class UserAccessService:
    def __init__(
        self,
        *,
        base_chat_config_path: Path,
        runtime_factory: Callable[[AuthenticatedUser], Any],
        users_db_path: Path | None = None,
        users_root_dir: Path | None = None,
        session_ttl_seconds: int = 12 * 60 * 60,
    ) -> None:
        self.account_store = UserAccountStore(
            base_chat_config_path=base_chat_config_path,
            users_db_path=users_db_path,
            users_root_dir=users_root_dir,
        )
        self.runtime_pool = UserRuntimePool(runtime_factory=runtime_factory)
        self.session_ttl_seconds = max(60, int(session_ttl_seconds))
        self._session_lock = threading.Lock()
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _issue_token(self, user: AuthenticatedUser) -> Dict[str, Any]:
        now = _now_utc()
        expires_at = now + timedelta(seconds=self.session_ttl_seconds)
        token = secrets.token_urlsafe(32)
        with self._session_lock:
            self._sessions[token] = {
                "username": user.username,
                "issued_at": _to_iso(now),
                "expires_at": _to_iso(expires_at),
            }
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": _to_iso(expires_at),
        }

    def _cleanup_expired_sessions(self) -> None:
        now = _now_utc()
        with self._session_lock:
            expired_tokens = []
            for token, session in self._sessions.items():
                try:
                    expires_at = datetime.fromisoformat(str(session.get("expires_at", "")).replace("Z", "+00:00"))
                except Exception:
                    expired_tokens.append(token)
                    continue
                if expires_at <= now:
                    expired_tokens.append(token)
            for token in expired_tokens:
                self._sessions.pop(token, None)

    def register_user(
        self,
        *,
        username: str,
        password: str,
        role: str = "basic",
        display_name: Optional[str] = None,
        assistant_name: Optional[str] = None,
        persona_prompt: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        user = self.account_store.register_user(
            username=username,
            password=password,
            role=role,
            display_name=display_name,
            assistant_name=assistant_name,
            persona_prompt=persona_prompt,
            workflow_id=workflow_id,
        )
        return {"user": user.to_payload()}

    def login(self, *, username: str, password: str) -> Dict[str, Any]:
        self._cleanup_expired_sessions()
        user = self.account_store.verify_credentials(username=username, password=password)
        token_payload = self._issue_token(user)
        return {
            "user": user.to_payload(),
            **token_payload,
        }

    def logout(self, token: str) -> None:
        with self._session_lock:
            self._sessions.pop(str(token or "").strip(), None)

    def authenticate(self, token: str) -> AuthenticatedUser:
        self._cleanup_expired_sessions()
        token_text = str(token or "").strip()
        if not token_text:
            raise UserAccessError("missing access token", status_code=401)
        with self._session_lock:
            session = self._sessions.get(token_text)
        if not isinstance(session, dict):
            raise UserAccessError("invalid or expired access token", status_code=401)
        username = str(session.get("username", "") or "").strip()
        if not username:
            raise UserAccessError("invalid or expired access token", status_code=401)
        user = self.account_store.get_user(username=username)
        if user is None:
            with self._session_lock:
                self._sessions.pop(token_text, None)
            raise UserAccessError("user no longer exists", status_code=401)
        return user

    def update_user_config(self, *, user: AuthenticatedUser, updates: Dict[str, Any]) -> Dict[str, Any]:
        updated_user = self.account_store.update_user_config(
            username=user.username,
            updates=updates,
        )
        self.runtime_pool.invalidate(updated_user.username)
        return {"user": updated_user.to_payload()}

    def get_user_config_schema(self, *, user: AuthenticatedUser) -> Dict[str, Any]:
        return self.account_store.get_user_config_schema(username=user.username)

    def get_runtime(self, *, user: AuthenticatedUser) -> Any:
        return self.runtime_pool.get_runtime(user)

    def health_payload(self) -> Dict[str, Any]:
        self._cleanup_expired_sessions()
        with self._session_lock:
            active_sessions = len(self._sessions)
        return {
            "users_db_path": str(self.account_store.users_db_path),
            "users_root_dir": str(self.account_store.users_root_dir),
            "user_count": self.account_store.user_count(),
            "active_sessions": active_sessions,
            "runtime_count": self.runtime_pool.runtime_count(),
            "session_ttl_seconds": self.session_ttl_seconds,
        }

    def shutdown(self) -> None:
        self.runtime_pool.shutdown()
