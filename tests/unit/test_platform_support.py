import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lan_input_share import platform_support
from lan_input_share.config.paths import AppPaths
from lan_input_share.core.models import DeviceRole


def test_mac_data_path_does_not_require_windows_environment(monkeypatch):
    monkeypatch.setattr(platform_support.sys, "platform", "darwin")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    paths = AppPaths.from_environment()
    assert paths.root == Path.home() / "Library" / "Application Support" / "LanInputShare"
    assert paths.staging_dir == paths.root / "staging"


def test_unsupported_platform_fails_before_loading_native_backend(monkeypatch):
    monkeypatch.setattr(platform_support.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="Windows.*macOS"):
        platform_support.create_input_hook()


@pytest.mark.parametrize("role", [DeviceRole.CLIENT, DeviceRole.CONTROLLER])
def test_missing_accessibility_explains_how_to_enable_it(monkeypatch, role):
    monkeypatch.setattr(platform_support.sys, "platform", "darwin")
    monkeypatch.setattr(
        platform_support,
        "permission_status",
        lambda: {
            "accessibility": False,
            "input_monitoring": True,
            "post_events": False,
        },
    )
    with pytest.raises(RuntimeError, match="辅助功能"):
        platform_support.ensure_permissions(role)


def test_input_monitoring_required_only_for_controller(monkeypatch):
    monkeypatch.setattr(platform_support.sys, "platform", "darwin")
    monkeypatch.setattr(
        platform_support,
        "permission_status",
        lambda: {
            "accessibility": True,
            "input_monitoring": False,
            "post_events": True,
        },
    )
    platform_support.ensure_permissions(DeviceRole.CLIENT)
    with pytest.raises(RuntimeError, match="输入监控"):
        platform_support.ensure_permissions(DeviceRole.CONTROLLER)


def test_retina_screen_size_uses_same_logical_units_as_cursor(monkeypatch):
    monkeypatch.setattr(platform_support.sys, "platform", "darwin")
    quartz = SimpleNamespace(
        CGMainDisplayID=lambda: 1,
        CGDisplayBounds=lambda _: SimpleNamespace(size=SimpleNamespace(width=1512, height=982)),
    )
    monkeypatch.setattr(platform_support, "_load_quartz", lambda: quartz)
    screen = platform_support.primary_screen()
    assert (screen.width, screen.height) == (1512, 982)


def test_mac_runtime_can_import_without_windows_native_apis():
    script = """
import ctypes
import sys
sys.platform = "darwin"
for name in ("windll", "WinDLL", "WINFUNCTYPE"):
    if hasattr(ctypes, name):
        delattr(ctypes, name)
import lan_input_share.app
import lan_input_share.runtime.service
assert not any(m in sys.modules for m in (
    "win32gui", "win32clipboard", "lan_input_share.input.win32_hook",
    "lan_input_share.input.win32_injector", "lan_input_share.input.win32_cursor",
))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
