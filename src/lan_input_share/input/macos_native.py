from __future__ import annotations

import importlib
import threading
from collections.abc import Callable
from typing import Any

from lan_input_share.input.events import Point

INJECTION_MARKER = 0x4C49534D
_lock = threading.Lock()
_remote_anchor: Point | None = None
_restore_cursor: Callable[[], None] | None = None


def load_quartz() -> Any:
    try:
        return importlib.import_module("Quartz")
    except ImportError as exc:
        raise RuntimeError("macOS 输入需要 pyobjc-framework-Quartz") from exc


def load_core_foundation() -> Any:
    return importlib.import_module("CoreFoundation")


def check_result(result: int, action: str) -> None:
    if result != 0:
        raise OSError(result, f"macOS {action}失败")


def register_remote_cursor(anchor: Point, restore: Callable[[], None]) -> None:
    global _remote_anchor, _restore_cursor
    with _lock:
        _remote_anchor = anchor
        _restore_cursor = restore


def remote_anchor() -> Point | None:
    with _lock:
        return _remote_anchor


def forget_remote_cursor(*, keep_restore: bool = False) -> None:
    global _remote_anchor, _restore_cursor
    with _lock:
        _remote_anchor = None
        if not keep_restore:
            _restore_cursor = None


def restore_remote_cursor() -> None:
    with _lock:
        restore = _restore_cursor
    if restore is not None:
        restore()


__all__ = ["INJECTION_MARKER", "load_quartz", "load_core_foundation"]
