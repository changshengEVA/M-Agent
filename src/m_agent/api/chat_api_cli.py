from __future__ import annotations

import argparse
import logging
from typing import Optional

import uvicorn

from m_agent.agents.chat_controller_agent import DEFAULT_CHAT_CONFIG_PATH
from m_agent.api.user_access import AuthenticatedUser, UserAccessService

from .chat_api_protocol import protocol_logger
from .chat_api_runtime import ChatServiceRuntime
from .chat_api_shared import _resolve_config_path, _resolve_optional_path
from .chat_api_web import create_app

logger = logging.getLogger(__name__)


def _configure_logging(debug: bool = False) -> None:
    root_level = logging.INFO if debug else logging.WARNING
    logging.basicConfig(
        level=root_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,
    )

    protocol_handler = logging.StreamHandler()
    protocol_handler.setLevel(logging.INFO)
    protocol_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
    protocol_logger.handlers = [protocol_handler]
    protocol_logger.setLevel(logging.INFO)
    protocol_logger.propagate = False

    if not debug:
        for noisy_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(noisy_name).setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the M-Agent chat API server with SSE events.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8777, help="Bind port. Default: 8777")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CHAT_CONFIG_PATH),
        help=f"Startup-fixed chat config path. Default: {DEFAULT_CHAT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--idle-flush-seconds",
        type=int,
        default=1800,
        help="Idle timeout in seconds before pending manual memory is auto-flushed. Default: 1800",
    )
    parser.add_argument(
        "--history-max-rounds",
        type=int,
        default=12,
        help="Max in-memory rounds retained per thread for chat history. Default: 12",
    )
    parser.add_argument(
        "--users-db",
        default="config/users/users.json",
        help="User auth database path. Default: config/users/users.json",
    )
    parser.add_argument(
        "--session-ttl-seconds",
        type=int,
        default=12 * 60 * 60,
        help="Auth session TTL in seconds. Default: 43200",
    )
    parser.add_argument(
        "--disable-auth",
        action="store_true",
        help="Disable register/login and run with startup-fixed anonymous runtime only.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose backend/module logs. Default mode keeps only concise HTTP/SSE protocol logs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _configure_logging(debug=bool(args.debug))
    config_path = _resolve_config_path(str(args.config or "").strip() or str(DEFAULT_CHAT_CONFIG_PATH))
    service_runtime = ChatServiceRuntime(
        config_path=config_path,
        idle_flush_seconds=int(args.idle_flush_seconds),
        history_max_rounds=int(args.history_max_rounds),
    )
    user_access: Optional[UserAccessService] = None
    if not bool(args.disable_auth):
        users_db_path = _resolve_optional_path(str(args.users_db or "").strip() or "config/users/users.json")

        def _runtime_factory(user: AuthenticatedUser) -> ChatServiceRuntime:
            return ChatServiceRuntime(
                config_path=user.config_path,
                idle_flush_seconds=int(args.idle_flush_seconds),
                history_max_rounds=int(args.history_max_rounds),
            )

        user_access = UserAccessService(
            base_chat_config_path=config_path,
            runtime_factory=_runtime_factory,
            users_db_path=users_db_path,
            session_ttl_seconds=int(args.session_ttl_seconds),
        )

    app = create_app(service_runtime=service_runtime, user_access=user_access)
    url = f"http://{args.host}:{args.port}"
    logger.info("M-Agent chat API listening on %s", url)
    logger.info("Startup config locked to %s", config_path)
    logger.info(
        "Thread memory runtime: idle_flush_seconds=%s history_max_rounds=%s",
        service_runtime.idle_flush_seconds,
        service_runtime.history_max_rounds,
    )
    if user_access is None:
        logger.info("Auth mode: disabled")
    else:
        logger.info(
            "Auth mode: enabled users_db=%s session_ttl_seconds=%s",
            args.users_db,
            int(args.session_ttl_seconds),
        )
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info" if args.debug else "warning",
            access_log=bool(args.debug),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down chat API...")
