"""Authenticated live login view. Page pixels and inputs are never persisted."""

import asyncio
import hashlib
import json
import re
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException

from backend.services.browser_account_service import website_url

_SCRIPT = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")


def managed_container(client, instance, *, missing_ok=False):
    """Resolve only a container whose writable Profile matches this account."""
    from docker.errors import NotFound

    host = urlparse(instance.endpoint).hostname or ""
    if not re.fullmatch(r"agent-[1-9]\d*", host):
        raise HTTPException(409, "该旧环境不支持内嵌登录，请新建账号以自动准备登录环境")
    try:
        container = client.containers.get(host)
    except NotFound:
        if missing_ok:
            return None
        raise HTTPException(409, "登录浏览器未运行，请重试准备登录环境") from None
    profile_key = hashlib.sha256(instance.profile_name.encode()).hexdigest()[:16]
    mounts = container.attrs.get("Mounts", [])
    matches = [m for m in mounts if m.get("Destination") == "/home/chrome/.config/chromium"]
    if len(matches) != 1 or not matches[0].get("Name", "").endswith(
        f"_browser_profile_{profile_key}"
    ):
        raise HTTPException(409, "登录环境与账号不匹配，已停止操作")
    return container


class _Connection:
    def __init__(self, client, container):
        execution = client.api.exec_create(
            container.id, ["node", "--input-type=module", "-e", _SCRIPT], stdin=True
        )
        self.connection = client.api.exec_start(execution["Id"], socket=True)
        self.transport = getattr(self.connection, "_sock", self.connection)
        if hasattr(self.transport, "settimeout"):
            self.transport.settimeout(15)
        self.lock = threading.Lock()
        self.used = time.monotonic()

    def close(self):
        self.connection.close()

    def request(self, payload):
        from docker.utils.socket import read

        # No credentials in process arguments, files or environment variables.
        self.transport.sendall((json.dumps(payload) + "\n").encode())

        def exact(size):
            result = bytearray()
            while len(result) < size:
                chunk = read(self.transport, size - len(result))
                if not chunk:
                    raise ValueError("Browser connection closed")
                result.extend(chunk)
            return bytes(result)

        output = bytearray()
        while True:
            header = exact(8)
            size = int.from_bytes(header[4:8], "big")
            if size > 12_000_000 or len(output) + size > 12_000_000:
                raise ValueError("Browser frame too large")
            chunk = exact(size)
            if header[0] == 1:
                output.extend(chunk)
                if output.endswith(b"\n"):
                    return json.loads(output)


_connections: dict[str, _Connection] = {}
_connection_guard = threading.Lock()
_creating: set[str] = set()


def _display(instance, payload):
    import docker

    with closing(docker.from_env(timeout=15)) as client:
        # Revalidate identity/mount on every request, even with a warm connection.
        container = managed_container(client, instance)
        if container.status != "running":
            container.start()
        key = container.id + ":" + instance.profile_name
        with _connection_guard:
            for old_key, old in list(_connections.items()):
                if time.monotonic() - old.used > 60 and old.lock.acquire(blocking=False):
                    try:
                        old.close()
                        del _connections[old_key]
                    finally:
                        old.lock.release()
            connection = _connections.get(key)
            if connection is None:
                if key in _creating:
                    raise HTTPException(409, "此账号正在连接，请稍后重试")
                if len(_connections) + len(_creating) >= 16:
                    raise HTTPException(503, "同时打开的登录页面较多，请关闭暂不用的页面后重试")
                _creating.add(key)
            else:
                if not connection.lock.acquire(blocking=False):
                    raise HTTPException(409, "登录操作正在进行，请稍后重试")
                connection.used = time.monotonic()
        if connection is None:
            try:
                connection = _Connection(client, container)
                connection.lock.acquire()
                with _connection_guard:
                    _connections[key] = connection
            finally:
                with _connection_guard:
                    _creating.discard(key)
        try:
            data = connection.request(payload)
            if "error" in data:
                raise HTTPException(503, "登录画面正在准备或连接中断，请稍后重试")
            return {
                key: data[key]
                for key in (
                    "image",
                    "width",
                    "height",
                    "title",
                    "origin",
                    "suggested_name",
                    "target_id",
                )
            }
        except Exception:
            # Never replay an input whose acknowledgement was lost.
            with _connection_guard:
                if _connections.get(key) is connection:
                    del _connections[key]
            connection.close()
            raise
        finally:
            connection.used = time.monotonic()
            connection.lock.release()


async def run_driver(function, *args):
    # HTTP disconnect/cancellation must not release account ownership while a
    # non-cancelable Docker thread is still typing or deleting its environment.
    work = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        try:
            await work
        finally:
            raise


async def display(account, instance, action: dict):
    if account.status in {"archived", "deleting"}:
        raise HTTPException(409, "账号已归档或正在删除")
    if (
        instance is None
        or instance.profile_name != account.profile_name
        or instance.profile_kind != "authenticated"
    ):
        raise HTTPException(409, "账号登录环境已改变")
    payload = {**action, "url": website_url(account), "target_id": account.login_target_id}
    try:
        return await run_driver(_display, instance, payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "无法连接账号登录画面，请检查浏览器服务后重试") from exc


def clear_managed_profile(instance):
    """Remove only an account-created container and its exact named volume."""
    import docker

    if not re.fullmatch(r"account-[a-f0-9]{32}", instance.profile_name):
        raise HTTPException(409, "该登录环境不是系统创建的，请选择保留登录数据后删除账号")
    with closing(docker.from_env(timeout=20)) as client:
        container = managed_container(client, instance, missing_ok=True)
        key = hashlib.sha256(instance.profile_name.encode()).hexdigest()[:16]
        if container:
            volume_names = [
                m["Name"]
                for m in container.attrs["Mounts"]
                if m.get("Destination") == "/home/chrome/.config/chromium"
            ]
            container.remove(force=True)
        else:
            # Retry cleanup after a previous attempt stopped the container.
            volume_names = [
                v.name for v in client.volumes.list() if v.name.endswith(f"_browser_profile_{key}")
            ]
        if len(volume_names) > 1:
            raise HTTPException(409, "存在多个同名登录环境，已停止清理")
        for name in volume_names:
            client.volumes.get(name).remove()  # no force: shared/in-use volumes must fail
