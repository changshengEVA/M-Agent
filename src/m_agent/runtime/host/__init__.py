"""Engine-neutral Runtime Host entry points."""

from .factory import create_runtime_host
from .protocol import RuntimeHost, ThreadEventEmitter
from .think_life_adapter import ThinkLifeRuntimeHost, as_runtime_host

__all__ = [
    "RuntimeHost",
    "ThreadEventEmitter",
    "ThinkLifeRuntimeHost",
    "as_runtime_host",
    "create_runtime_host",
]
