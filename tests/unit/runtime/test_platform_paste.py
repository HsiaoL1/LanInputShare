import pytest

from lan_input_share.config.models import AppConfig
from lan_input_share.config.paths import AppPaths
from lan_input_share.input.events import KeyEvent
from lan_input_share.runtime.service import NetworkRoleService


@pytest.mark.parametrize("platform,modifier", [("win32", 0x11), ("darwin", 0x5B)])
def test_compatibility_paste_releases_modifier_after_native_injection_failure(
    monkeypatch,
    tmp_path,
    platform,
    modifier,
):
    paths = AppPaths(
        tmp_path,
        tmp_path / "config.json",
        tmp_path / "logs",
        tmp_path / "identity",
        tmp_path / "staging",
    )
    service = NetworkRoleService(AppConfig.default(device_name="test"), paths)
    calls = []

    class Injector:
        def inject(self, event):
            calls.append(event)
            if event.virtual_key == 0x56 and event.is_down:
                raise OSError("native event failed")

    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.setattr("lan_input_share.runtime.service.create_injector", Injector)
    with pytest.raises(OSError):
        service._replay_paste()
    assert KeyEvent(47, 0x56, False) in calls
    assert any(event.virtual_key == modifier and not event.is_down for event in calls)
