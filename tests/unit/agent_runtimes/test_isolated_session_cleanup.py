"""Real Linux process-group checks; runnable with stdlib unittest in the API image."""

import asyncio
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path

from backend.agent_runtimes.isolated_session_adapter import (
    _stop_probe_process,
    _stop_process,
)

_CHILD = (
    "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
    "print('ready',flush=True); time.sleep(30)"
)
_PARENT = (
    "import subprocess,sys; "
    f"child=subprocess.Popen([sys.executable,'-c',{_CHILD!r}],stdout=subprocess.PIPE); "
    "child.stdout.readline(); print(child.pid,flush=True)"
)


async def _assert_dead(child_pid):
    async with asyncio.timeout(2):
        while True:
            try:
                process_stat = Path(f"/proc/{child_pid}/stat").read_text()
            except FileNotFoundError:
                return
            if process_stat.split(") ", 1)[1].startswith("Z "):
                # The container supervisor may not have reaped the dead orphan yet.
                return
            await asyncio.sleep(0.01)


def _kill_fixture_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux /proc and process groups")
class IsolatedSessionCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_cleanup_kills_descendant_after_parent_exits(self):
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _PARENT,
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            child_pid = int(await asyncio.wait_for(proc.stdout.readline(), timeout=2))
            await asyncio.wait_for(proc.wait(), timeout=2)
            self.assertEqual(proc.returncode, 0)
            await asyncio.wait_for(_stop_process(proc), timeout=3)
            await _assert_dead(child_pid)
        finally:
            _kill_fixture_group(proc.pid)
            await asyncio.wait_for(proc.wait(), timeout=2)

    async def test_probe_cleanup_kills_descendant_after_parent_exits(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", _PARENT],
            stdout=subprocess.PIPE,
            start_new_session=True,
            bufsize=0,
        )
        try:
            child_pid = int(await asyncio.wait_for(asyncio.to_thread(proc.stdout.readline), 2))
            proc.wait(timeout=2)
            self.assertEqual(proc.returncode, 0)
            await asyncio.wait_for(asyncio.to_thread(_stop_probe_process, proc), timeout=3)
            await _assert_dead(child_pid)
        finally:
            _kill_fixture_group(proc.pid)
            proc.wait(timeout=2)
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
