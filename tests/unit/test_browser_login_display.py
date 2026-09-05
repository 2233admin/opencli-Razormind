import asyncio
import hashlib
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend.services.browser_login_display import (
    clear_managed_profile,
    managed_container,
    run_driver,
)


def test_profile_mismatch_never_opens_or_removes_another_account(monkeypatch):
    client = Mock()
    container = client.containers.get.return_value
    container.attrs = {
        "Mounts": [{"Destination": "/home/chrome/.config/chromium", "Name": "other_profile"}]
    }
    instance = SimpleNamespace(endpoint="http://agent-2:19222", profile_name="account-" + "a" * 32)
    with pytest.raises(HTTPException) as error:
        managed_container(client, instance)
    assert error.value.status_code == 409
    container.remove.assert_not_called()


def test_clear_only_verified_account_volume(monkeypatch):
    import docker

    client = Mock()
    monkeypatch.setattr(docker, "from_env", lambda **_: client)
    instance = SimpleNamespace(endpoint="http://agent-2:19222", profile_name="account-" + "a" * 32)
    volume = (
        "preview_browser_profile_" + hashlib.sha256(instance.profile_name.encode()).hexdigest()[:16]
    )
    container = client.containers.get.return_value
    container.attrs = {"Mounts": [{"Destination": "/home/chrome/.config/chromium", "Name": volume}]}
    clear_managed_profile(instance)
    container.remove.assert_called_once_with(force=True)
    client.volumes.get.assert_called_once_with(volume)
    client.volumes.get.return_value.remove.assert_called_once_with()
    client.close.assert_called_once()


@pytest.mark.asyncio
async def test_cancellation_waits_for_actual_driver_completion():
    started = threading.Event()
    finish = threading.Event()

    def driver():
        started.set()
        assert finish.wait(3)

    task = asyncio.create_task(run_driver(driver))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_connection_reused_and_failed_input_never_replayed(monkeypatch):
    import docker

    from backend.services import browser_login_display as display

    client = Mock()
    container = SimpleNamespace(id="verified", status="running")
    monkeypatch.setattr(docker, "from_env", lambda **_: client)
    verify = Mock(return_value=container)
    monkeypatch.setattr(display, "managed_container", verify)
    monkeypatch.setattr(display, "_connections", {})
    connection = SimpleNamespace(lock=threading.Lock(), used=0, close=Mock(), request=Mock())
    frame = dict(
        image="pixels", width=10, height=10, title="", origin="", suggested_name="", target_id="one"
    )
    connection.request.return_value = frame

    def connect(*_):
        assert display._connection_guard.acquire(blocking=False), "Docker I/O held global lock"
        display._connection_guard.release()
        return connection

    factory = Mock(side_effect=connect)
    monkeypatch.setattr(display, "_Connection", factory)
    instance = SimpleNamespace(profile_name="one")
    for _ in range(2):
        assert display._display(instance, {"kind": "frame"}) == frame
    assert factory.call_count == 1
    assert verify.call_count == 2
    connection.request.side_effect = OSError("connection lost after input")
    with pytest.raises(OSError):
        display._display(instance, {"kind": "text", "text": "private"})
    assert connection.request.call_count == 3
    assert display._connections == {}
    assert not connection.lock.locked()
    connection.close.assert_called_once()
