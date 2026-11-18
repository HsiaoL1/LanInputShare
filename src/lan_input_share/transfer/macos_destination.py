from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Protocol

from lan_input_share.transfer.windows_destination import DestinationKind, ResolvedDestination

_FINDER_DIRECTORY_SCRIPT = '''tell application "Finder"
    if not frontmost then return "__NOT_FINDER__"
    if (count of windows) is 0 then return "__VIRTUAL__"
    set selectedWindow to front window
    if class of selectedWindow is not Finder window then return "__VIRTUAL__"
    try
        set targetFolder to target of selectedWindow
        if class of targetFolder is not folder and class of targetFolder is not disk then
            return "__VIRTUAL__"
        end if
        set targetPath to POSIX path of (targetFolder as alias)
        if not frontmost then return "__NOT_FINDER__"
        if id of front window is not id of selectedWindow then return "__VIRTUAL__"
        return targetPath
    on error
        return "__VIRTUAL__"
    end try
end tell'''


def _load_native() -> tuple[ModuleType, ModuleType]:
    if sys.platform != "darwin":
        raise RuntimeError("Finder 目标解析只支持 macOS")
    return importlib.import_module("AppKit"), importlib.import_module("objc")


class FinderForegroundProvider(Protocol):
    def is_finder(self) -> bool: ...


class FinderDirectoryProvider(Protocol):
    def resolve(self) -> str | None: ...


class MacOSForegroundApplicationProvider:
    def is_finder(self) -> bool:
        appkit, objc = _load_native()
        with objc.autorelease_pool():
            application = appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
            return application is not None and application.bundleIdentifier() == "com.apple.finder"


class _FinderNotForegroundError(RuntimeError):
    pass


class AppleScriptFinderDirectoryProvider:
    def __init__(self, *, timeout: float = 2.0) -> None:
        if timeout <= 0:
            raise ValueError("Finder 自动化超时必须为正数")
        self._timeout = timeout

    def resolve(self) -> str | None:
        # A constant argv script never interpolates clipboard filenames or other
        # user input, never uses a shell, and performs its own foreground recheck.
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", _FINDER_DIRECTORY_SCRIPT],
            capture_output=True,
            text=True,
            timeout=self._timeout,
            check=False,
        )
        if result.returncode:
            if "-1743" in result.stderr:
                raise PermissionError("Finder Automation permission denied")
            raise OSError("Finder Automation query failed")
        # Only remove osascript's line terminator; folder names can contain spaces.
        candidate = result.stdout.removesuffix("\n").removesuffix("\r")
        if candidate == "__NOT_FINDER__":
            raise _FinderNotForegroundError
        return None if candidate in {"", "__VIRTUAL__"} else candidate


class FinderDestinationResolver:
    def __init__(
        self,
        foreground: FinderForegroundProvider | None = None,
        shell: FinderDirectoryProvider | None = None,
    ) -> None:
        self._foreground = foreground or MacOSForegroundApplicationProvider()
        self._shell = shell or AppleScriptFinderDirectoryProvider()

    async def resolve_foreground(self) -> ResolvedDestination:
        try:
            if not self._foreground.is_finder():
                return _compatibility("not_finder")
        except Exception:
            return _compatibility("window_unavailable")
        try:
            candidate = await asyncio.to_thread(self._shell.resolve)
        except _FinderNotForegroundError:
            return _compatibility("not_finder")
        except PermissionError:
            return _compatibility("automation_denied")
        except subprocess.TimeoutExpired:
            return _compatibility("shell_timeout")
        except Exception:
            return _compatibility("shell_error")
        if not candidate:
            return _compatibility("virtual_location")
        try:
            path = Path(candidate)
            if not path.is_absolute():
                return _compatibility("virtual_location")
            path = path.resolve(strict=True)
            if not path.is_dir():
                return _compatibility("not_directory")
        except (OSError, RuntimeError, ValueError):
            return _compatibility("not_directory")
        return ResolvedDestination(DestinationKind.DIRECT, path, None)


def _compatibility(reason: str) -> ResolvedDestination:
    return ResolvedDestination(DestinationKind.COMPATIBILITY, None, reason)


__all__ = [
    "AppleScriptFinderDirectoryProvider",
    "FinderDestinationResolver",
    "FinderDirectoryProvider",
    "FinderForegroundProvider",
    "MacOSForegroundApplicationProvider",
]
