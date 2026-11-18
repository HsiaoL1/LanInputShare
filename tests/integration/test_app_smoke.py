from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

import lan_input_share.app as app_module
from lan_input_share.app import (
    ApplicationCoordinator,
    SingleInstanceGuard,
    _ConfiguredRuntime,
    diagnose,
    main,
)
from lan_input_share.config.models import AppConfig
from lan_input_share.config.paths import AppPaths
from lan_input_share.config.store import ConfigStore
from lan_input_share.core.models import DeviceRole
from lan_input_share.ui.close_behavior import CloseChoice
from lan_input_share.ui.main_window import MainWindow
from lan_input_share.ui.view_model import AppViewModel


class Resource:
    def __init__(self, name: str, events: list[str], fail: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail = fail

    async def start(self) -> None:
        self.events.append(f"start:{self.name}")
        if self.fail:
            raise RuntimeError("boom")

    async def stop(self) -> None:
        self.events.append(f"stop:{self.name}")


class Safety:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def restore_local_input(self) -> None:
        self.events.append("restore")


class Guard:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired
        self.focused = 0

    def acquire(self) -> bool:
        return self.acquired

    def focus_existing(self) -> None:
        self.focused += 1

    def release(self) -> None:
        pass


@pytest.mark.asyncio
async def test_app_starts_in_configured_role_and_stops_all_resources_in_reverse_order() -> None:
    events: list[str] = []
    app = ApplicationCoordinator(
        [Resource("network", events), Resource("clipboard", events), Resource("hooks", events)],
        Safety(events),
        Guard(True),
    )

    assert await app.start() is True
    await app.stop()

    assert events == [
        "start:network",
        "start:clipboard",
        "start:hooks",
        "restore",
        "stop:hooks",
        "stop:clipboard",
        "stop:network",
    ]


def test_diagnose_mode_does_not_install_hooks_or_modify_firewall(
    monkeypatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "lan_input_share.app.diagnose",
        lambda: {"ok": True, "mode": "read_only"},
    )

    assert main(["--diagnose"]) == 0

    assert json.loads(capsys.readouterr().out)["mode"] == "read_only"


def test_gui_smoke_mode_loads_qt_without_starting_the_desktop_runtime() -> None:
    assert main(["--gui-smoke-test"]) == 0
    from lan_input_share.ui.resources import application_icon

    application = QApplication.instance()
    assert isinstance(application, QApplication)
    assert application.windowIcon().cacheKey() == application_icon().cacheKey()


def test_export_diagnostics_button_saves_both_application_and_crash_logs(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = AppPaths(
        root=tmp_path,
        config_file=tmp_path / "config.json",
        log_dir=tmp_path / "logs",
        identity_dir=tmp_path / "identity",
        staging_dir=tmp_path / "staging",
    )
    paths.log_dir.mkdir()
    (paths.log_dir / "lan-input-share.log").write_text("current event\n", encoding="utf-8")
    (paths.log_dir / "lan-input-share.log.1").write_text("older event\n", encoding="utf-8")
    (paths.log_dir / "native-crash.log").write_text("traceback\n", encoding="utf-8")
    store = ConfigStore(paths.config_file)
    store.save(AppConfig.default(device_name="Mac Test", role=DeviceRole.CLIENT))
    view_model = AppViewModel(object())
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    destination = tmp_path / "Mac-Test.zip"
    messages: list[str] = []
    errors: list[str] = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(destination), ""))
    monkeypatch.setattr(
        QMessageBox, "information", lambda _parent, _title, message: messages.append(message)
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda _parent, _title, message: errors.append(message)
    )
    monkeypatch.setattr(
        "lan_input_share.ui.main_window.ask_close_choice",
        lambda _parent: (CloseChoice.QUIT, False),
    )
    monkeypatch.setattr(app_module, "list_adapters", lambda: ())
    app_module._bind_diagnostics_export(view_model, window, paths, store)

    button = next(
        button for button in window.findChildren(QPushButton) if button.text() == "导出诊断与日志"
    )
    button.click()

    with zipfile.ZipFile(destination) as bundle:
        assert json.loads(bundle.read("report.json"))["config"]["device_name"] == "Mac Test"
        assert bundle.read("logs/lan-input-share.log") == b"current event\n"
        assert bundle.read("logs/lan-input-share.log.1") == b"older event\n"
        assert bundle.read("logs/native-crash.log") == b"traceback\n"
    assert messages and str(destination) in messages[0]
    assert errors == []


def test_packaging_spec_embeds_runtime_and_executable_icon() -> None:
    spec = (Path(__file__).parents[2] / "packaging" / "lan-input-share.spec").read_text(
        encoding="utf-8"
    )

    assert "lan-input-share.ico" in spec
    assert "datas=" in spec
    assert "icon=" in spec


def test_packaging_spec_drops_codex_poppler_dlls_from_analysis_path() -> None:
    spec = (Path(__file__).parents[2] / "packaging" / "lan-input-share.spec").read_text(
        encoding="utf-8"
    )

    assert "codex-runtimes" in spec
    assert "native\\\\poppler" in spec
    assert "a.binaries =" in spec


@pytest.mark.asyncio
async def test_second_local_instance_focuses_existing_instance_instead_of_starting_hooks() -> None:
    events: list[str] = []
    guard = Guard(False)
    app = ApplicationCoordinator([Resource("hooks", events)], Safety(events), guard)

    assert await app.start() is False

    assert events == []
    assert guard.focused == 1


def test_single_instance_guard_uses_injected_activator_for_hidden_window() -> None:
    titles: list[str] = []
    guard = SingleInstanceGuard(window_activator=titles.append)

    guard.focus_existing()

    assert titles == ["局域网键鼠共享"]


def test_live_diagnose_returns_version_paths_and_adapter_inventory() -> None:
    result = diagnose()

    assert result["ok"] is True
    assert result["version"]
    assert "paths" in result
    assert "adapters" in result


def test_background_service_start_failure_is_reported_and_cleaned_up(
    monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    stopped = threading.Event()

    class FailingService:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        async def start(self) -> None:
            raise OSError("address already in use")

        async def stop(self) -> None:
            stopped.set()

    monkeypatch.setattr("lan_input_share.app.NetworkRoleService", FailingService)
    paths = AppPaths(
        tmp_path,
        tmp_path / "config.json",
        tmp_path / "logs",
        tmp_path / "identity",
        tmp_path / "staging",
    )
    reported = threading.Event()
    messages: list[str] = []

    def status(message: str) -> None:
        messages.append(message)
        reported.set()

    runtime = _ConfiguredRuntime(ConfigStore(paths.config_file), paths, status=status)
    try:
        runtime.start(
            AppConfig(
                version=1,
                device_name="desk",
                role=DeviceRole.CONTROLLER,
                adapter_id=None,
                bind_ip="127.0.0.1",
                controller_host=None,
                port=24870,
                layout=("desk",),
            )
        )

        assert reported.wait(timeout=2)
        assert stopped.wait(timeout=2)
        assert any("address already in use" in message for message in messages)
    finally:
        runtime.shutdown()


def test_rapid_reconfigure_serializes_service_replacement(
    monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    lock = threading.Lock()
    second_started = threading.Event()
    active = 0
    peak_active = 0
    starts = 0

    class TrackingService:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            self.running = False

        async def start(self) -> None:
            nonlocal active, peak_active, starts
            await __import__("asyncio").sleep(0.05)
            with lock:
                self.running = True
                active += 1
                starts += 1
                peak_active = max(peak_active, active)
                if starts == 2:
                    second_started.set()

        async def stop(self) -> None:
            nonlocal active
            with lock:
                if self.running:
                    self.running = False
                    active -= 1

    monkeypatch.setattr("lan_input_share.app.NetworkRoleService", TrackingService)
    paths = AppPaths(
        tmp_path,
        tmp_path / "config.json",
        tmp_path / "logs",
        tmp_path / "identity",
        tmp_path / "staging",
    )
    runtime = _ConfiguredRuntime(ConfigStore(paths.config_file), paths)
    config = AppConfig.default(device_name="desk", role=DeviceRole.CLIENT)
    try:
        runtime.configure(config)
        runtime.configure(config)

        assert second_started.wait(timeout=3)
        assert peak_active == 1
    finally:
        runtime.shutdown()

    assert active == 0
