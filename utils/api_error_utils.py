from __future__ import annotations

import socket
from typing import Iterator


_NETWORK_CLASS_TOKENS = (
    "apiconnectionerror",
    "apitimeouterror",
    "connecterror",
    "connecttimeout",
    "readtimeout",
    "writetimeout",
    "pooltimeout",
    "timeout",
    "networkerror",
    "transporterror",
    "remoteprotocolerror",
    "proxyerror",
    "connectionerror",
    "connectionreseterror",
    "connectionabortederror",
    "connectionrefusederror",
    "gaierror",
)

_NETWORK_MESSAGE_TOKENS = (
    "connection error",
    "connection aborted",
    "connection refused",
    "connection reset",
    "connection timeout",
    "connection timed out",
    "server disconnected",
    "failed to resolve",
    "name or service not known",
    "nodename nor servname provided",
    "temporary failure in name resolution",
    "timed out",
    "timeout",
    "network is unreachable",
    "remote end closed connection",
    "transport error",
    "proxy error",
    "dns",
    "eof occurred in violation of protocol",
    "incomplete chunked read",
    "peer closed connection without sending complete message body",
    "ssl",
    "tls",
)


def iter_exception_chain(exc: BaseException | None) -> Iterator[BaseException]:
    seen: set[int] = set()
    current = exc
    while current is not None:
        obj_id = id(current)
        if obj_id in seen:
            break
        seen.add(obj_id)
        yield current
        current = current.__cause__ or current.__context__


def is_network_error_text(text: object) -> bool:
    message = str(text or "").strip().lower()
    if not message:
        return False
    return any(token in message for token in _NETWORK_MESSAGE_TOKENS)


def is_network_api_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False

    for current in iter_exception_chain(exc):
        if isinstance(
            current,
            (
                TimeoutError,
                ConnectionError,
                socket.timeout,
                socket.gaierror,
                socket.herror,
            ),
        ):
            return True

        cls_name = f"{type(current).__module__}.{type(current).__name__}".lower()
        if any(token in cls_name for token in _NETWORK_CLASS_TOKENS):
            return True

        message = str(current).strip().lower()
        if is_network_error_text(message):
            return True

    return False
