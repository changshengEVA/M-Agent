from __future__ import annotations

import base64
import logging
import math
import mimetypes
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)

_DEFAULT_GMAIL_SCOPES: Tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
)


class GmailDependencyError(RuntimeError):
    """Raised when Gmail API dependencies are not installed."""


class GmailAuthError(RuntimeError):
    """Raised when Gmail OAuth credentials cannot be acquired."""


@dataclass
class GmailClientConfig:
    user_id: str = "me"
    credentials_path: Optional[Path] = None
    token_path: Optional[Path] = None
    scopes: Tuple[str, ...] = field(default_factory=lambda: _DEFAULT_GMAIL_SCOPES)
    allow_local_webserver_flow: bool = True
    allow_console_flow: bool = False
    request_timeout_seconds: float = 10.0
    oauth_flow_timeout_seconds: int = 300

    def __post_init__(self) -> None:
        try:
            timeout = float(self.request_timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("request_timeout_seconds must be a positive number") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("request_timeout_seconds must be a positive number")
        self.request_timeout_seconds = timeout
        try:
            oauth_timeout = int(self.oauth_flow_timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "oauth_flow_timeout_seconds must be a positive integer"
            ) from exc
        if oauth_timeout <= 0:
            raise ValueError(
                "oauth_flow_timeout_seconds must be a positive integer"
            )
        self.oauth_flow_timeout_seconds = oauth_timeout


class GmailApiClient:
    """Thin wrapper around Gmail REST API."""

    def __init__(self, *, config: GmailClientConfig, service: Any = None) -> None:
        self.config = config
        self._service = service

    @staticmethod
    def _import_google_deps() -> Dict[str, Any]:
        try:
            from google.oauth2.credentials import Credentials  # type: ignore
            from google_auth_httplib2 import AuthorizedHttp, Request  # type: ignore
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
            from httplib2 import Http  # type: ignore
        except ModuleNotFoundError as exc:
            raise GmailDependencyError(
                "Gmail API dependencies are missing. Install: "
                "google-api-python-client, google-auth-httplib2, google-auth-oauthlib"
            ) from exc
        return {
            "Request": Request,
            "Credentials": Credentials,
            "AuthorizedHttp": AuthorizedHttp,
            "InstalledAppFlow": InstalledAppFlow,
            "Http": Http,
            "build": build,
        }

    def _get_service(self, *, force_reauth: bool = False) -> Any:
        if self._service is not None and not force_reauth:
            return self._service
        self._service = self._build_service(force_reauth=force_reauth)
        return self._service

    def _build_service(self, *, force_reauth: bool = False) -> Any:
        deps = self._import_google_deps()
        Request = deps["Request"]
        Credentials = deps["Credentials"]
        AuthorizedHttp = deps["AuthorizedHttp"]
        InstalledAppFlow = deps["InstalledAppFlow"]
        Http = deps["Http"]
        build = deps["build"]
        transport = Http(timeout=self.config.request_timeout_seconds)
        refresh_request = Request(transport)

        creds: Any = None
        token_path = self.config.token_path
        scopes = list(self.config.scopes)

        if token_path and token_path.exists() and not force_reauth:
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), scopes)
            except Exception:
                logger.info(
                    "Saved Gmail OAuth token could not be loaded; starting a new OAuth flow.",
                    exc_info=True,
                )
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(refresh_request)
            except Exception:
                logger.info(
                    "Saved Gmail OAuth token could not be refreshed; starting a new OAuth flow.",
                    exc_info=True,
                )
                creds = None

        if not creds or not creds.valid:
            creds = self._run_oauth_flow(InstalledAppFlow)

        if not creds or not creds.valid:
            raise GmailAuthError("Failed to obtain valid Gmail OAuth credentials.")

        if token_path:
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")

        authorized_http = AuthorizedHttp(creds, http=transport)
        return build(
            "gmail",
            "v1",
            http=authorized_http,
            cache_discovery=False,
        )

    @staticmethod
    def _is_auth_failure(exc: BaseException) -> bool:
        resp = getattr(exc, "resp", None)
        status = getattr(resp, "status", None)
        try:
            status_code = int(status)
        except Exception:
            status_code = 0
        # 403 is also used for quota/rate-limit failures.  Reauthenticating
        # those requests can open an OAuth flow without fixing the problem.
        if status_code == 401:
            return True

        text = str(exc).lower()
        auth_markers = (
            "invalid_grant",
            "invalid credentials",
            "unauthorized",
            "insufficient authentication",
            "insufficient permission",
            "insufficientpermissions",
        )
        return any(marker in text for marker in auth_markers)

    def _execute_request(self, request_factory: Any) -> Dict[str, Any]:
        service = self._get_service()
        try:
            return request_factory(service).execute() or {}
        except Exception as exc:
            if not self._is_auth_failure(exc):
                raise
            logger.info(
                "Gmail API request was rejected by auth; starting OAuth re-authentication.",
                exc_info=True,
            )
            self._service = None

        try:
            service = self._get_service(force_reauth=True)
            return request_factory(service).execute() or {}
        except GmailAuthError:
            raise
        except Exception as exc:
            if self._is_auth_failure(exc):
                raise GmailAuthError(
                    "Gmail authentication is required. Complete the OAuth flow and retry the email action."
                ) from exc
            raise

    def _run_oauth_flow(self, installed_app_flow_cls: Any) -> Any:
        credentials_path = self.config.credentials_path
        if credentials_path is None or not credentials_path.exists():
            raise GmailAuthError(
                "Gmail OAuth client credentials file is required. "
                "Set gmail.credentials_path in EmailAgent config."
            )

        flow = installed_app_flow_cls.from_client_secrets_file(
            str(credentials_path),
            list(self.config.scopes),
        )

        if self.config.allow_local_webserver_flow:
            try:
                return flow.run_local_server(
                    port=0,
                    timeout_seconds=self.config.oauth_flow_timeout_seconds,
                )
            except Exception:
                if not self.config.allow_console_flow:
                    raise
                logger.info(
                    "Gmail local OAuth flow failed; falling back to console flow.",
                    exc_info=True,
                )
        if self.config.allow_console_flow:
            run_console = getattr(flow, "run_console", None)
            if callable(run_console):
                return run_console()
            raise GmailAuthError(
                "Console OAuth is unavailable in this google-auth-oauthlib "
                "version. Complete the local browser flow instead."
            )
        raise GmailAuthError(
            "No OAuth flow is enabled. Enable gmail.oauth.allow_local_webserver_flow "
            "or gmail.oauth.allow_console_flow."
        )

    def search_threads(
        self,
        *,
        query: str,
        max_results: int = 20,
        page_token: Optional[str] = None,
        include_spam_trash: bool = False,
        label_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        return self._execute_request(
            lambda service: service.users()
            .threads()
            .list(
                userId=self.config.user_id,
                q=str(query or "").strip(),
                maxResults=max(1, int(max_results)),
                pageToken=page_token,
                includeSpamTrash=bool(include_spam_trash),
                labelIds=list(label_ids) if label_ids else None,
            )
        )

    def get_profile(self) -> Dict[str, Any]:
        """Return the authenticated mailbox profile.

        The profile's ``historyId`` is the safe baseline for incremental
        mailbox observers.  Keeping this transport primitive here avoids
        making background observers call the user-facing ``EmailAgent``.
        """

        return self._execute_request(
            lambda service: service.users().getProfile(
                userId=self.config.user_id,
            )
        )

    def list_history(
        self,
        *,
        start_history_id: str,
        max_results: int = 100,
        page_token: Optional[str] = None,
        label_id: Optional[str] = None,
        history_types: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """List mailbox changes after a Gmail ``historyId`` cursor."""

        start_id = str(start_history_id or "").strip()
        if not start_id:
            raise ValueError("start_history_id is required")
        return self._execute_request(
            lambda service: service.users()
            .history()
            .list(
                userId=self.config.user_id,
                startHistoryId=start_id,
                maxResults=max(1, min(500, int(max_results))),
                pageToken=page_token,
                labelId=str(label_id or "").strip() or None,
                historyTypes=(
                    list(history_types)
                    if history_types
                    else ["messageAdded"]
                ),
            )
        )

    def search_messages(
        self,
        *,
        query: str,
        max_results: int = 20,
        page_token: Optional[str] = None,
        include_spam_trash: bool = False,
        label_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        return self._execute_request(
            lambda service: service.users()
            .messages()
            .list(
                userId=self.config.user_id,
                q=str(query or "").strip(),
                maxResults=max(1, int(max_results)),
                pageToken=page_token,
                includeSpamTrash=bool(include_spam_trash),
                labelIds=list(label_ids) if label_ids else None,
            )
        )

    def get_thread(
        self,
        *,
        thread_id: str,
        fmt: str = "metadata",
        metadata_headers: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        return self._execute_request(
            lambda service: service.users()
            .threads()
            .get(
                userId=self.config.user_id,
                id=str(thread_id),
                format=fmt,
                metadataHeaders=list(metadata_headers) if metadata_headers else None,
            )
        )

    def get_message(
        self,
        *,
        message_id: str,
        fmt: str = "metadata",
        metadata_headers: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        return self._execute_request(
            lambda service: service.users()
            .messages()
            .get(
                userId=self.config.user_id,
                id=str(message_id),
                format=fmt,
                metadataHeaders=list(metadata_headers) if metadata_headers else None,
            )
        )

    def send_raw_message(self, *, raw_message: str) -> Dict[str, Any]:
        return self._execute_request(
            lambda service: service.users()
            .messages()
            .send(
                userId=self.config.user_id,
                body={"raw": str(raw_message or "").strip()},
            )
        )

    @staticmethod
    def build_raw_message(
        *,
        to: Sequence[str],
        subject: str,
        body_text: str,
        cc: Optional[Sequence[str]] = None,
        bcc: Optional[Sequence[str]] = None,
        body_html: Optional[str] = None,
        reply_to: Optional[str] = None,
        attachments: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> str:
        if not to:
            raise ValueError("`to` is required to build Gmail raw message.")

        msg = EmailMessage()
        msg["To"] = ", ".join(str(x).strip() for x in to if str(x).strip())
        if cc:
            msg["Cc"] = ", ".join(str(x).strip() for x in cc if str(x).strip())
        if bcc:
            msg["Bcc"] = ", ".join(str(x).strip() for x in bcc if str(x).strip())
        if reply_to:
            msg["Reply-To"] = str(reply_to).strip()
        msg["Subject"] = str(subject or "").strip()
        msg.set_content(str(body_text or ""))
        if body_html:
            msg.add_alternative(str(body_html), subtype="html")

        for attachment in attachments or []:
            filename = str(attachment.get("filename") or "attachment.bin").strip() or "attachment.bin"
            content_type = str(attachment.get("content_type") or "").strip()
            if not content_type:
                guessed_type, _ = mimetypes.guess_type(filename)
                content_type = guessed_type or "application/octet-stream"

            if "/" in content_type:
                maintype, subtype = content_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"

            content_bytes = GmailApiClient._attachment_to_bytes(attachment)
            msg.add_attachment(content_bytes, maintype=maintype, subtype=subtype, filename=filename)

        raw_bytes = base64.urlsafe_b64encode(msg.as_bytes())
        return raw_bytes.decode("utf-8")

    @staticmethod
    def _attachment_to_bytes(attachment: Dict[str, Any]) -> bytes:
        if "content_bytes" in attachment:
            value = attachment.get("content_bytes")
            if isinstance(value, bytes):
                return value
            if isinstance(value, str):
                return value.encode("utf-8")

        if "content_base64" in attachment:
            value = str(attachment.get("content_base64") or "").strip()
            if value:
                return base64.b64decode(value)

        if "path" in attachment:
            path = Path(str(attachment.get("path") or "").strip())
            if path.exists():
                return path.read_bytes()

        raise ValueError("Attachment must provide content_bytes, content_base64, or path.")
