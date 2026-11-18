"""Exclude Windows-native tests before they can import Windows-only libraries."""

import sys
from pathlib import Path

import pytest


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if sys.platform == "win32":
        return None
    path = collection_path.as_posix()
    if "/unit/windows" in path:
        return True
    if collection_path.name in {
        "test_native_write.py",
        "test_cursor_visibility.py",
        "test_two_process_control.py",
        "test_windows_destination_integration.py",
    } or collection_path.name.startswith("test_win32_"):
        return True
    return None
