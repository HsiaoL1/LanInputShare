import pytest
from test_controller_runtime import FakeCursor, FakeHook, FakeSession

from lan_input_share.core.models import DeviceInfo, DeviceRole, ScreenInfo
from lan_input_share.input.events import KeyEvent, Suppression
from lan_input_share.routing.controller import RoutingController, SendRemote
from lan_input_share.routing.layout import LinearLayout
from lan_input_share.runtime.controller import ControllerRuntime, decode_input_event


@pytest.mark.parametrize(
    "source,target,key,want",
    [
        ("win32", "darwin", KeyEvent(29, 0x11, True), KeyEvent(91, 0x5B, True, True)),
        ("win32", "darwin", KeyEvent(29, 0xA3, False, True), KeyEvent(92, 0x5C, False, True)),
        ("darwin", "win32", KeyEvent(91, 0x5B, True, True), KeyEvent(29, 0x11, True)),
        ("darwin", "win32", KeyEvent(92, 0x5C, False, True), KeyEvent(29, 0x11, False, True)),
        ("win32", "darwin", KeyEvent(30, 0x41, True), KeyEvent(30, 0x41, True)),
        ("darwin", "darwin", KeyEvent(29, 0x11, True), KeyEvent(29, 0x11, True)),
    ],
)
@pytest.mark.asyncio
async def test_sent_modifiers_match_target_shortcut_conventions(source, target, key, want):
    local = DeviceInfo("local", "local", DeviceRole.CONTROLLER, ScreenInfo(100, 100))
    peer = DeviceInfo("peer", "peer", DeviceRole.CLIENT, ScreenInfo(100, 100), platform=target)
    routing = RoutingController(
        "local", {"local": local, "peer": peer}, LinearLayout(("local", "peer"))
    )
    runtime = ControllerRuntime(routing, FakeHook(), FakeCursor(), local_platform=source)
    session = FakeSession(peer)
    runtime.attach_session(session)
    await runtime._execute_actions((SendRemote("peer", key),))
    assert decode_input_event(session.sent[0].payload) == want


@pytest.mark.asyncio
async def test_mac_command_v_intercepts_file_paste_and_its_key_release():
    local = DeviceInfo("local", "local", DeviceRole.CONTROLLER, ScreenInfo(100, 100))
    routing = RoutingController("local", {"local": local}, LinearLayout(("local",)))
    targets = []
    runtime = ControllerRuntime(
        routing,
        FakeHook(),
        FakeCursor(),
        local_platform="darwin",
        paste_handler=lambda t: targets.append(t) or True,
    )
    await runtime.start()
    try:
        runtime._on_hook_event(KeyEvent(91, 0x5B, True, True))
        assert runtime._on_hook_event(KeyEvent(47, 0x56, True)) is Suppression.SUPPRESS_ALWAYS
        assert runtime._on_hook_event(KeyEvent(47, 0x56, False)) is Suppression.SUPPRESS_ALWAYS
        assert targets == ["local"]
    finally:
        await runtime.stop()
