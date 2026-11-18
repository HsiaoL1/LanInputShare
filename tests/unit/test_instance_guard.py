import uuid
from types import SimpleNamespace

from lan_input_share.app import SingleInstanceGuard


def test_mac_instance_lock_releases_for_next_process(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    name = "test-" + uuid.uuid4().hex
    first = SingleInstanceGuard(name)
    second = SingleInstanceGuard(name)
    try:
        assert first.acquire()
        assert not second.acquire()
        first.release()
        assert second.acquire()
    finally:
        first.release()
        second.release()


def test_second_mac_instance_activates_process_recorded_in_lock(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    activations = []
    application = SimpleNamespace(activateWithOptions_=activations.append)
    running = SimpleNamespace(
        runningApplicationWithProcessIdentifier_=lambda pid: application if pid == 345 else None
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "AppKit",
        SimpleNamespace(
            NSRunningApplication=running,
            NSApplicationActivateIgnoringOtherApps=1,
        ),
    )
    guard = SingleInstanceGuard()
    guard._lock = SimpleNamespace(getLockInfo=lambda: (345, "host", "LanInputShare"))
    guard.focus_existing()
    assert activations == [1]
