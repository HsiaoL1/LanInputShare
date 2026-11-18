"""Select native desktop backends without importing another platform's APIs."""

from __future__ import annotations

import ctypes
import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from lan_input_share.clipboard.models import ClipboardImage
from lan_input_share.core.models import DeviceRole, ScreenInfo
from lan_input_share.runtime.client import InputInjector
from lan_input_share.runtime.controller import CursorController, InputHook
from lan_input_share.transfer.windows_destination import ResolvedDestination


class ClipboardWatcher(Protocol):
    def start(self, callback: Callable[[], None]) -> None: ...
    def stop(self) -> None: ...
    def read_marker(self) -> str: ...
    def read_unicode_text(self) -> str: ...
    def write_unicode_text(self, text: str, marker: str) -> None: ...
    def read_image(self) -> ClipboardImage | None: ...
    def write_image(self, image: ClipboardImage, marker: str) -> None: ...
    def read_file_drop(self) -> tuple[Path, ...]: ...
    def write_file_drop(self, paths: tuple[Path, ...], marker: str) -> None: ...


class DestinationResolver(Protocol):
    async def resolve_foreground(self) -> ResolvedDestination: ...


def _supported_platform() -> str:
    if sys.platform not in {"win32", "darwin"}:
        raise RuntimeError("目前仅支持 Windows 10/11 和 macOS 13 或更高版本")
    return sys.platform


def create_input_hook() -> InputHook:
    if _supported_platform() == "win32":
        from lan_input_share.input.win32_hook import Win32InputHook

        return Win32InputHook()
    from lan_input_share.input.macos_hook import MacOSInputHook

    return MacOSInputHook()


def create_cursor() -> CursorController:
    if _supported_platform() == "win32":
        from lan_input_share.input.win32_cursor import Win32CursorController

        return Win32CursorController()
    from lan_input_share.input.macos_cursor import MacOSCursorController

    return MacOSCursorController()


def create_injector() -> InputInjector:
    if _supported_platform() == "win32":
        from lan_input_share.input.win32_injector import Win32InputInjector

        return Win32InputInjector()
    from lan_input_share.input.macos_injector import MacOSInputInjector

    return MacOSInputInjector()


def create_clipboard() -> ClipboardWatcher:
    if _supported_platform() == "win32":
        from lan_input_share.clipboard.win32_clipboard import Win32ClipboardWatcher

        return Win32ClipboardWatcher()
    from lan_input_share.clipboard.macos_clipboard import MacOSClipboardWatcher

    return MacOSClipboardWatcher()


def create_destination_resolver() -> DestinationResolver:
    if _supported_platform() == "win32":
        from lan_input_share.transfer.windows_destination import ExplorerDestinationResolver

        return ExplorerDestinationResolver()
    from lan_input_share.transfer.macos_destination import FinderDestinationResolver

    return FinderDestinationResolver()


def _load_quartz() -> Any:
    return importlib.import_module("Quartz")


def primary_screen() -> ScreenInfo:
    _supported_platform()
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        return ScreenInfo(int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))
    quartz = _load_quartz()
    bounds = quartz.CGDisplayBounds(quartz.CGMainDisplayID())
    return ScreenInfo(round(bounds.size.width), round(bounds.size.height))


def permission_status() -> dict[str, bool]:
    if _supported_platform() == "win32":
        return {"accessibility": True, "input_monitoring": True, "post_events": True}
    quartz = _load_quartz()
    accessibility = importlib.import_module("ApplicationServices")
    return {
        "accessibility": bool(accessibility.AXIsProcessTrusted()),
        "input_monitoring": bool(quartz.CGPreflightListenEventAccess()),
        "post_events": bool(quartz.CGPreflightPostEventAccess()),
    }


def ensure_permissions(role: DeviceRole) -> None:
    _supported_platform()
    if sys.platform != "darwin":
        return
    try:
        permissions = permission_status()
    except ImportError as exc:
        raise RuntimeError(
            "macOS 原生依赖未安装，请执行 python -m pip install -e '.[dev]'"
        ) from exc
    missing: list[str] = []
    if not permissions["accessibility"] or not permissions["post_events"]:
        missing.append("辅助功能")
    if role is DeviceRole.CONTROLLER and not permissions["input_monitoring"]:
        missing.append("输入监控")
    if missing:
        names = "、".join(missing)
        raise RuntimeError(
            f"请在系统设置 → 隐私与安全性 → {names} 中允许 LanInputShare，"
            "从源码运行时允许所用终端/Python，然后退出并重新打开程序。"
        )
