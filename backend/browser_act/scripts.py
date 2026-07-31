"""run_pack_script — the second subprocess hop for a browser-act pack.

The first hop is browser-act's own CLI (backend/browser_act/cli.py); this one
runs a vendored pack's scripts/*.py, which are pure
JS emitters (argparse -> print(js_string), no LLM/no network/no file I/O --
see backend/browser_act_packs/VENDOR.md).

Same subprocess-safety pattern as backend/channels/cli_channel.py and
backend/browser_act/cli.py's _run(): asyncio.create_subprocess_exec +
asyncio.wait_for(timeout) + TimeoutError -> proc.kill() + await proc.wait()
so a timed-out script is never orphaned.

Both subprocess hops are argv-only, never shell. This hop invokes
``sys.executable <script_path> <args...>`` via
create_subprocess_exec -- caller params travel as separate argv elements,
never string-interpolated into a shell command.
"""

import asyncio
import sys
from pathlib import Path


class ScriptError(Exception):
    """Raised on a non-zero pack script exit or a timed-out call.

    This hop passes no env secrets (pack scripts take only argv params, see
    module docstring), but the message still never echoes more than argv +
    captured stderr -- mirroring ``BrowserActError``'s guarantee (backend/
    browser_act/cli.py) even though there's no env dict here to leak.
    """


async def run_pack_script(
    script_path: str | Path,
    args: list[str],
    *,
    timeout: float | None = None,
) -> str:
    """Run a vendored pack's ``scripts/x.py <args>`` and return its stdout
    (the JS string it emits).

    Never ``shell=True`` / ``create_subprocess_shell`` -- ``args`` travels
    straight to ``create_subprocess_exec`` as separate argv elements
    alongside ``sys.executable`` and the script path. Caller params (e.g. a
    search keyword) are therefore never interpolated into a shell string,
    matching architecture decision #6.

    ``timeout`` defaults to ``get_settings().browser_act_timeout`` (the same
    setting the browser-act CLI hop uses) when not given explicitly.
    """
    if timeout is None:
        from backend.config import get_settings

        timeout = get_settings().browser_act_timeout

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script_path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        # wait_for only cancels communicate() -- the child itself keeps
        # running until explicitly killed (mirrors cli.py's _run exactly, so
        # a timed-out pack script is never orphaned).
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        raise ScriptError(
            f"pack script {script_path} timed out after {timeout}s"
        ) from exc

    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise ScriptError(
            f"pack script {script_path} exited with code {proc.returncode}: {stderr_text}"
        )

    return stdout_text
