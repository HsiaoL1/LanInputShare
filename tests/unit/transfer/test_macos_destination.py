from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from lan_input_share.transfer.windows_destination import DestinationKind


class FakeForeground:
    def __init__(self, finder: bool = True) -> None:
        self.finder = finder

    def is_finder(self) -> bool:
        return self.finder


class FakeDirectory:
    def __init__(self, result: str | None = None, *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.thread: int | None = None

    def resolve(self) -> str | None:
        self.calls += 1
        self.thread = threading.get_ident()
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_resolves_only_foreground_finder_directory_off_event_loop(tmp_path: Path) -> None:
    from lan_input_share.transfer.macos_destination import FinderDestinationResolver

    folder = tmp_path / "目录 with spaces"
    folder.mkdir()
    shell = FakeDirectory(str(folder))
    event_loop_thread = threading.get_ident()

    result = await FinderDestinationResolver(FakeForeground(), shell).resolve_foreground()

    assert result.kind is DestinationKind.DIRECT
    assert result.path == folder.resolve()
    assert result.reason is None
    assert shell.thread != event_loop_thread


@pytest.mark.asyncio
async def test_non_finder_foreground_avoids_automation_query() -> None:
    from lan_input_share.transfer.macos_destination import FinderDestinationResolver

    shell = FakeDirectory("/not-to-be-read")

    result = await FinderDestinationResolver(FakeForeground(False), shell).resolve_foreground()

    assert result.kind is DestinationKind.COMPATIBILITY
    assert result.path is None
    assert result.reason == "not_finder"
    assert shell.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", [None, "", "x-apple-search://recent", "relative/path"])
async def test_virtual_or_relative_directory_safely_falls_back(candidate: str | None) -> None:
    from lan_input_share.transfer.macos_destination import FinderDestinationResolver

    result = await FinderDestinationResolver(
        FakeForeground(), FakeDirectory(candidate)
    ).resolve_foreground()

    assert result.kind is DestinationKind.COMPATIBILITY
    assert result.path is None
    assert result.reason == "virtual_location"


@pytest.mark.asyncio
@pytest.mark.parametrize("make_file", [False, True])
async def test_missing_or_non_directory_path_safely_falls_back(
    tmp_path: Path, make_file: bool
) -> None:
    from lan_input_share.transfer.macos_destination import FinderDestinationResolver

    candidate = tmp_path / "candidate"
    if make_file:
        candidate.write_text("file", encoding="utf-8")

    result = await FinderDestinationResolver(
        FakeForeground(), FakeDirectory(str(candidate))
    ).resolve_foreground()

    assert result.kind is DestinationKind.COMPATIBILITY
    assert result.reason == "not_directory"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PermissionError("private Automation diagnostic"), "automation_denied"),
        (subprocess.TimeoutExpired("osascript", 2), "shell_timeout"),
        (OSError("private folder diagnostic"), "shell_error"),
    ],
)
async def test_native_failures_are_sanitized_and_fall_back(error: Exception, reason: str) -> None:
    from lan_input_share.transfer.macos_destination import FinderDestinationResolver

    result = await FinderDestinationResolver(
        FakeForeground(), FakeDirectory(error=error)
    ).resolve_foreground()

    assert result.kind is DestinationKind.COMPATIBILITY
    assert result.reason == reason
    assert "private" not in repr(result)


def test_native_directory_query_uses_constant_script_argv_and_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from lan_input_share.transfer.macos_destination import AppleScriptFinderDirectoryProvider

    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=str(tmp_path) + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    result = AppleScriptFinderDirectoryProvider(timeout=1.5).resolve()

    assert result == str(tmp_path)
    args, kwargs = calls[0]
    assert args[:2] == ["/usr/bin/osascript", "-e"]
    assert str(tmp_path) not in args[2]
    assert 'tell application "Finder"' in args[2]
    assert "frontmost" in args[2]
    assert kwargs == {"capture_output": True, "text": True, "timeout": 1.5, "check": False}


@pytest.mark.parametrize(
    ("exit_code", "stdout", "stderr", "expected_error"),
    [
        (1, "", "Not authorized to send Apple events to Finder. (-1743)", PermissionError),
        (1, "", "Finder error (-1728)", OSError),
    ],
)
def test_native_automation_errors_are_classified(
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    stdout: str,
    stderr: str,
    expected_error: type[Exception],
) -> None:
    from lan_input_share.transfer.macos_destination import AppleScriptFinderDirectoryProvider

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, exit_code, stdout, stderr),
    )

    with pytest.raises(expected_error):
        AppleScriptFinderDirectoryProvider().resolve()


@pytest.mark.asyncio
async def test_frontmost_change_during_script_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from lan_input_share.transfer.macos_destination import (
        AppleScriptFinderDirectoryProvider,
        FinderDestinationResolver,
    )

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "__NOT_FINDER__\n", ""),
    )

    result = await FinderDestinationResolver(
        FakeForeground(), AppleScriptFinderDirectoryProvider()
    ).resolve_foreground()

    assert result.kind is DestinationKind.COMPATIBILITY
    assert result.reason == "not_finder"


def test_native_foreground_reads_bundle_identifier_under_autorelease_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lan_input_share.transfer import macos_destination

    active = False

    class Pool:
        def __enter__(self):  # type: ignore[no-untyped-def]
            nonlocal active
            active = True

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            nonlocal active
            active = False

    def frontmost():  # type: ignore[no-untyped-def]
        assert active
        return SimpleNamespace(bundleIdentifier=lambda: "com.apple.finder")

    appkit = SimpleNamespace(
        NSWorkspace=SimpleNamespace(
            sharedWorkspace=lambda: SimpleNamespace(frontmostApplication=frontmost)
        )
    )
    monkeypatch.setattr(
        macos_destination, "_load_native", lambda: (appkit, SimpleNamespace(autorelease_pool=Pool))
    )

    assert macos_destination.MacOSForegroundApplicationProvider().is_finder()
    assert not active
