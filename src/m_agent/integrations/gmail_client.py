from __future__ import annotations

import base64
import logging
import mimetypes
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)

_DEFAULT_GMAIL_SCOPES: Tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
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


class GmailApiClient:
    """Thin wrapper around Gmail REST API."""

    def __init__(self, *, config: GmailClientConfig, service: Any = None) -> None:
        self.config = config
        self._service = service

    @staticmethod
    def _import_google_deps() -> Dict[str, Any]:
        try:
            from google.auth.transport.requests import Request  # type: ignore
            from google.oauth2.credentials import Credentials  # type: ignore
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
        except ModuleNotFoundError as exc:
            raise GmailDependencyError(
                "Gmail API dependencies are missing. Install: "
                "google-api-python-client, google-auth-httplib2, google-auth-oauthlib"
            ) from exc
        return {
            "Request": Request,
            "Credentials": Credentials,
            "InstalledAppFlow": InstalledAppFlow,
            "build": build,
        }

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service
        self._service = self._build_service()
        return self._service

    def _build_service(self) -> Any:
        deps = self._import_google_deps()
        Request = deps["Request"]
        Credentials = deps["Credentials"]
        InstalledAppFlow = deps["InstalledAppFlow"]
        build = deps["build"]

        creds: Any = None
        token_path = self.config.token_path
        scopes = list(self.config.scopes)

        if token_path and token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), scopes)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        if not creds or not creds.valid:
            creds = self._run_oauth_flow(InstalledAppFlow)

        if not creds or not creds.valid:
            raise GmailAuthError("Failed to obtain valid Gmail OAuth credentials.")

        if token_path:
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("gmail", "v1", credentials=creds, cache_discovery=False)

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
            return flow.run_local_server(port=0)
        if self.config.allow_console_flow:
            return flow.run_console()
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
        service = self._get_service()
        request = (
            service.users()
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
        return request.execute() or {}

    def search_messages(
        self,
        *,
        query: str,
        max_results: int = 20,
        page_token: Optional[str] = None,
        include_spam_trash: bool = False,
        label_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        service = self._get_service()
        request = (
            service.users()
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
        return request.execute() or {}

    def get_thread(
        self,
        *,
        thread_id: str,
        fmt: str = "metadata",
        metadata_headers: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        service = self._get_service()
        request = (
            service.users()
            .threads()
            .get(
                userId=self.config.user_id,
                id=str(thread_id),
                format=fmt,
                metadataHeaders=list(metadata_headers) if metadata_headers else None,
            )
        )
        return request.execute() or {}

    def get_message(
        self,
        *,
        message_id: str,
        fmt: str = "metadata",
        metadata_headers: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        service = self._get_service()
        request = (
            service.users()
            .messages()
            .get(
                userId=self.config.user_id,
                id=str(message_id),
                format=fmt,
                metadataHeaders=list(metadata_headers) if metadata_headers else None,
            )
        )
        return request.execute() or {}

    def send_raw_message(self, *, raw_message: str) -> Dict[str, Any]:
        service = self._get_service()
        request = (
            service.users()
            .messages()
            .send(
                userId=self.config.user_id,
                body={"raw": str(raw_message or "").strip()},
            )
        )
        return request.execute() or {}

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
