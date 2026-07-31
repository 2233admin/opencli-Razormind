"""Unit tests for BrowserActChannel (Browser Act integration PR-C/PR-E).

Builds a SYNTHETIC pack under tmp_path so these tests never depend on a real
PR-D manifest or a real browser: the browser-act CLI hop is mocked at
``backend.browser_act.cli._run`` (the same seam test_cli.py itself patches
one layer up), while the SECOND subprocess hop (``run_pack_script``) runs
for real via ``sys.executable`` -- proving the actual script-execution
machinery works, not just a mock of it.

The credential tests (PR-E, decision #7) additionally route
``backend.database.AsyncSessionLocal`` at a per-test in-memory engine (same
pattern as ``tests/unit/auth/test_manager.py``) so ``AuthManager.store`` /
``.resolve`` exercise the REAL encrypted round-trip, not a mock.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.auth import crypto
from backend.auth.manager import AuthManager
from backend.browser_act import cli as browser_act_cli
from backend.browser_act.cli import BrowserActResult
from backend.browser_act.scripts import run_pack_script
from backend.browser_act_packs.catalog import PackCatalog
from backend.channels.browser_act_channel import BrowserActChannel

PACK_SELECTOR = "search-research/demo-search"


def _write_synthetic_pack(root: Path, *, pagination: dict | None = None) -> Path:
    """Build <root>/search-research/demo-search/{SKILL.md, scripts/emit.py,
    channel.manifest.json}. Returns the pack directory."""
    pack_dir = root / "search-research" / "demo-search"
    scripts_dir = pack_dir / "scripts"
    scripts_dir.mkdir(parents=True)

    (pack_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-search\n"
        'description: "synthetic pack for BrowserActChannel unit tests"\n'
        "---\n\n"
        "# Demo Search\n",
        encoding="utf-8",
    )

    (scripts_dir / "emit.py").write_text(
        "import argparse\n"
        "\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('keyword')\n"
        "    args = parser.parse_args()\n"
        "    print(args.keyword)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )

    manifest = {
        "domain": "search-research",
        "capability": "demo-search",
        "param_schema": [
            {"name": "keyword", "required": True},
            {"name": "page", "required": False, "default": "1"},
        ],
        "steps": [
            {
                "op": "navigate",
                "url_template": "https://example.com/search?q={keyword}&page={page}",
            },
            {"op": "wait", "wait_mode": "stable"},
            {"op": "eval_script", "script": "scripts/emit.py", "args": ["{keyword}"]},
        ],
        "pagination": pagination or {"mode": "none"},
        "success": {"min_count": 1},
    }
    (pack_dir / "channel.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack_dir


def _run_side_effect(eval_responses: list[str]):
    """Build an async side_effect for backend.browser_act.cli._run that
    answers "eval" subcommands from eval_responses in order (repeating the
    last one if exhausted) and no-ops every other subcommand
    (navigate/wait/click/input)."""
    responses = list(eval_responses)
    state = {"i": 0}

    async def _side_effect(args, *, timeout=None, env=None):
        subcommand = args[2] if len(args) > 2 else None
        if subcommand == "eval":
            i = min(state["i"], len(responses) - 1)
            state["i"] += 1
            return BrowserActResult(returncode=0, stdout=responses[i], stderr="")
        return BrowserActResult(returncode=0, stdout="", stderr="")

    return _side_effect


@pytest.fixture
def channel(tmp_path):
    _write_synthetic_pack(tmp_path)
    return BrowserActChannel(catalog=PackCatalog(root=tmp_path))


# ── collect(): happy path ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_happy_path(channel):
    eval_json = json.dumps([{"id": 1, "title": "Alpha"}, {"id": 2, "title": "Beta"}])
    with patch("backend.browser_act.cli._run", side_effect=_run_side_effect([eval_json])):
        result = await channel.collect(
            {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}}, {}
        )

    assert result.success is True
    assert result.items == [{"id": 1, "title": "Alpha"}, {"id": 2, "title": "Beta"}]
    assert result.metadata["pack"] == PACK_SELECTOR
    assert result.metadata["pages_fetched"] == 1
    assert result.metadata["mode"] == "chrome-direct"


# ── collect(): needs_human vs generic error ──────────────────────────────


@pytest.mark.asyncio
async def test_collect_needs_human_on_login_wall(channel):
    eval_json = json.dumps({"error": True, "message": "please login to continue"})
    with patch("backend.browser_act.cli._run", side_effect=_run_side_effect([eval_json])):
        result = await channel.collect(
            {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}}, {}
        )

    assert result.success is False
    assert result.error_type == "needs_human"
    assert "login" in result.error.lower()


@pytest.mark.asyncio
async def test_collect_generic_error_not_misclassified_as_needs_human(channel):
    eval_json = json.dumps({"error": True, "message": "selector not found"})
    with patch("backend.browser_act.cli._run", side_effect=_run_side_effect([eval_json])):
        result = await channel.collect(
            {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}}, {}
        )

    assert result.success is False
    assert result.error_type == "error"


# ── collect(): pagination (url_page mode + stop_when) ────────────────────


@pytest.mark.asyncio
async def test_collect_paginates_and_stops_on_stop_when(tmp_path):
    _write_synthetic_pack(
        tmp_path,
        pagination={
            "mode": "url_page",
            "url_template": "https://example.com/search?q={keyword}&page={page}",
            "page_param": "page",
            "stop_when": "result_count < 2",
        },
    )
    channel = BrowserActChannel(catalog=PackCatalog(root=tmp_path))

    page1 = json.dumps([{"id": 1}, {"id": 2}])
    page2 = json.dumps([{"id": 3}])  # 1 item < 2 -> stop_when triggers after this page
    with patch(
        "backend.browser_act.cli._run", side_effect=_run_side_effect([page1, page2])
    ):
        result = await channel.collect(
            {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}}, {}
        )

    assert result.success is True
    assert len(result.items) == 3
    assert result.metadata["pages_fetched"] == 2


# ── collect(): below min_count ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_below_min_count_fails(tmp_path):
    pack_dir = _write_synthetic_pack(tmp_path)
    manifest_path = pack_dir / "channel.manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["success"]["min_count"] = 5
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    channel = BrowserActChannel(catalog=PackCatalog(root=tmp_path))

    eval_json = json.dumps([{"id": 1}])
    with patch("backend.browser_act.cli._run", side_effect=_run_side_effect([eval_json])):
        result = await channel.collect(
            {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}}, {}
        )

    assert result.success is False
    assert result.error_type == "error"
    assert "min_count" in result.error


@pytest.mark.asyncio
async def test_collect_template_param_mismatch_returns_error_not_crash(tmp_path):
    """A manifest whose step template references a param name absent from
    ctx (param_schema/step authoring mismatch) must surface as a
    ChannelResult error, not an uncaught KeyError from str.format()."""
    pack_dir = _write_synthetic_pack(tmp_path)
    manifest_path = pack_dir / "channel.manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["steps"][0]["url_template"] = "https://example.com/search?q={keyword}&x={typo_param}"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    channel = BrowserActChannel(catalog=PackCatalog(root=tmp_path))

    with patch("backend.browser_act.cli._run", side_effect=_run_side_effect(["{}"])):
        result = await channel.collect(
            {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}}, {}
        )

    assert result.success is False
    assert result.error_type == "error"


# ── validate_config ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_config_unknown_pack(channel):
    errors = await channel.validate_config({"pack": "nonexistent/pack"})
    assert errors != []


@pytest.mark.asyncio
async def test_validate_config_missing_required_param(channel):
    errors = await channel.validate_config({"pack": PACK_SELECTOR, "params": {}})
    assert any("keyword" in e for e in errors)


@pytest.mark.asyncio
async def test_validate_config_bad_mode(channel):
    errors = await channel.validate_config(
        {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}, "mode": "stealthy"}
    )
    assert any("mode" in e for e in errors)


@pytest.mark.asyncio
async def test_validate_config_valid(channel):
    errors = await channel.validate_config(
        {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}}
    )
    assert errors == []


@pytest.mark.asyncio
async def test_validate_config_no_manifest_reports_pr_d_note(tmp_path):
    """A pack with no channel.manifest.json at all (real state for ~75 of the
    78 vendored packs until PR-D) is a valid, documented error -- not a
    crash."""
    pack_dir = tmp_path / "search-research" / "no-manifest-pack"
    (pack_dir / "scripts").mkdir(parents=True)
    (pack_dir / "SKILL.md").write_text(
        "---\nname: no-manifest-pack\ndescription: \"x\"\n---\n", encoding="utf-8"
    )
    channel = BrowserActChannel(catalog=PackCatalog(root=tmp_path))

    errors = await channel.validate_config({"pack": "search-research/no-manifest-pack"})
    assert any("channel.manifest.json" in e for e in errors)


# ── health_check ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_check_success(channel):
    with patch(
        "backend.browser_act.cli.version", AsyncMock(return_value="browser-act 2.0.2")
    ):
        assert await channel.health_check() is True


@pytest.mark.asyncio
async def test_health_check_browser_act_error_returns_false(channel):
    with patch(
        "backend.browser_act.cli.version",
        AsyncMock(side_effect=browser_act_cli.BrowserActError("boom")),
    ):
        assert await channel.health_check() is False


@pytest.mark.asyncio
async def test_health_check_binary_not_installed_returns_false(channel):
    with patch(
        "backend.browser_act.cli.version", AsyncMock(side_effect=FileNotFoundError())
    ):
        assert await channel.health_check() is False


# ── secret-not-leaked ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_secret_not_leaked_in_error(channel, monkeypatch):
    monkeypatch.setenv("BROWSER_ACT_API_KEY", "sekret")

    async def _failing_run(args, *, timeout=None, env=None):
        raise browser_act_cli.BrowserActError(
            f"browser-act {' '.join(args)} exited with code 1: auth failed"
        )

    with patch("backend.browser_act.cli._run", side_effect=_failing_run):
        result = await channel.collect(
            {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}}, {}
        )

    assert result.success is False
    assert "sekret" not in (result.error or "")
    assert "sekret" not in json.dumps(result.metadata)


# ── registry ──────────────────────────────────────────────────────────────


def test_browser_act_registered():
    from backend.channels.registry import get_channel

    instance = get_channel("browser_act")
    assert isinstance(instance, BrowserActChannel)


# ── injection safety on the script hop (run_pack_script) ─────────────────


@pytest.mark.asyncio
async def test_run_pack_script_injection_safety(tmp_path):
    """A param containing shell metachars must never be split or
    interpolated into a shell string -- create_subprocess_exec is used (not
    _shell), and the dangerous string reaches the script as ONE verbatim
    argv element."""
    script = tmp_path / "echo_argv.py"
    script.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")
    dangerous = "foo(); rm -rf / #"

    with patch("asyncio.create_subprocess_shell") as spawn_shell:
        output = await run_pack_script(script, [dangerous])

    spawn_shell.assert_not_called()
    assert output.strip() == dangerous


# ── credentials (Browser Act integration PR-E, decision #7) ────────────────────────────────
#
# AuthManager.store()/.resolve() open their own session via
# backend.database.AsyncSessionLocal -- these tests point that at a per-test
# in-memory engine (identical pattern to tests/unit/auth/test_manager.py) so
# the credential round-trip is REAL encryption/decryption, not a mock.


def _sessionmaker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_stealth_resolves_stored_key_and_injects_session_env(
    channel, db_engine, monkeypatch
):
    """store() an encrypted key, then collect(mode=stealth, source_id=...)
    must resolve it and pass it to the browser-act CLI hop's subprocess env
    under BROWSER_ACT_API_KEY_ENV -- proven by capturing the ``env`` kwarg
    every ``backend.browser_act.cli._run`` call receives (the CLI wrapper
    PR-B/PR-C already drive), not by inspecting BrowserActChannel internals."""
    monkeypatch.setenv(crypto.ENV_KEY, Fernet.generate_key().decode())
    sm = _sessionmaker(db_engine)

    eval_json = json.dumps([{"id": 1, "title": "Alpha"}])
    captured_envs: list[dict | None] = []

    async def _run_capture(args, *, timeout=None, env=None):
        captured_envs.append(env)
        subcommand = args[2] if len(args) > 2 else None
        if subcommand == "eval":
            return BrowserActResult(returncode=0, stdout=eval_json, stderr="")
        return BrowserActResult(returncode=0, stdout="", stderr="")

    with patch("backend.database.AsyncSessionLocal", sm):
        await AuthManager().store("src-stealth-1", "browser_act_api_key", "real-secret-key")

        # Prove the round-trip is real: the DB row holds ciphertext, not the
        # plaintext key, and decrypting it independently recovers the value
        # AuthManager.resolve() is about to hand the channel.
        from sqlalchemy import select

        from backend.models.source_credential import SourceCredential

        async with sm() as session:
            row = (
                await session.execute(
                    select(SourceCredential).where(
                        SourceCredential.source_id == "src-stealth-1"
                    )
                )
            ).scalar_one()
        assert row.ciphertext != "real-secret-key"
        assert crypto.decrypt(row.ciphertext) == "real-secret-key"

        with patch("backend.browser_act.cli._run", side_effect=_run_capture):
            result = await channel.collect(
                {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}, "mode": "stealth"},
                {},
                source_id="src-stealth-1",
            )

    assert result.success is True
    assert any(
        env and env.get("BROWSER_ACT_API_KEY") == "real-secret-key" for env in captured_envs
    )


@pytest.mark.asyncio
async def test_stealth_missing_key_errors_and_never_runs_keyless(channel, db_engine, monkeypatch):
    """mode=stealth with NO stored key (source_id given, but nothing was ever
    stored for it) must fail loudly with the documented message and must
    NEVER call the browser-act CLI hop at all -- no keyless fallback."""
    monkeypatch.setenv(crypto.ENV_KEY, Fernet.generate_key().decode())
    sm = _sessionmaker(db_engine)

    called = False

    async def _run_should_not_be_called(args, *, timeout=None, env=None):
        nonlocal called
        called = True
        return BrowserActResult(returncode=0, stdout="[]", stderr="")

    with patch("backend.database.AsyncSessionLocal", sm):
        with patch("backend.browser_act.cli._run", side_effect=_run_should_not_be_called):
            result = await channel.collect(
                {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}, "mode": "stealth"},
                {},
                source_id="src-no-key",
            )

    assert result.success is False
    assert result.error_type == "error"
    assert "stealth mode requires a BrowserAct API key" in result.error
    assert "browser_act_api_key" in result.error
    assert called is False


@pytest.mark.asyncio
async def test_stealth_missing_key_without_source_id_also_errors(channel):
    """No source_id at all (collect() called directly, bypassing the
    runner) must be treated identically to "no key stored" for stealth --
    best-effort resolution, never a fabricated source id."""
    result = await channel.collect(
        {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}, "mode": "stealth"}, {}
    )
    assert result.success is False
    assert "stealth mode requires a BrowserAct API key" in result.error


@pytest.mark.asyncio
async def test_chrome_direct_no_key_runs_fine(channel, db_engine, monkeypatch):
    """mode=chrome-direct (default) needs no key at all: a source_id with
    nothing stored for it still collects successfully."""
    monkeypatch.setenv(crypto.ENV_KEY, Fernet.generate_key().decode())
    sm = _sessionmaker(db_engine)
    eval_json = json.dumps([{"id": 1, "title": "Alpha"}])

    with patch("backend.database.AsyncSessionLocal", sm):
        with patch(
            "backend.browser_act.cli._run", side_effect=_run_side_effect([eval_json])
        ):
            result = await channel.collect(
                {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}, "mode": "chrome-direct"},
                {},
                source_id="src-chrome-direct-no-key",
            )

    assert result.success is True
    assert result.metadata["mode"] == "chrome-direct"


@pytest.mark.asyncio
async def test_no_leak_when_key_stored_and_a_later_error_occurs(channel, db_engine, monkeypatch):
    """A key IS stored (stealth mode resolves + injects it), but the pack
    script itself then reports a generic (non-auth) error -- the resolved
    key string must never appear in result.error or result.metadata."""
    monkeypatch.setenv(crypto.ENV_KEY, Fernet.generate_key().decode())
    sm = _sessionmaker(db_engine)
    secret = "sk-leak-canary-9f3a"

    error_json = json.dumps({"error": True, "message": "selector not found"})

    with patch("backend.database.AsyncSessionLocal", sm):
        await AuthManager().store("src-leak-check", "browser_act_api_key", secret)

        with patch(
            "backend.browser_act.cli._run", side_effect=_run_side_effect([error_json])
        ):
            result = await channel.collect(
                {"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}, "mode": "stealth"},
                {},
                source_id="src-leak-check",
            )

    assert result.success is False
    assert result.error_type == "error"
    assert secret not in (result.error or "")
    assert secret not in json.dumps(result.metadata)


@pytest.mark.asyncio
async def test_fetch_threads_ctx_source_id_into_collect_for_credential_resolution(
    channel, db_engine, monkeypatch
):
    """The thick-contract fetch() (what run_channel() actually calls in
    production) must pass ctx.source_id through so credential resolution
    works on the only path that ever has a real DataSource id."""
    from backend.channels.base import FetchContext

    monkeypatch.setenv(crypto.ENV_KEY, Fernet.generate_key().decode())
    sm = _sessionmaker(db_engine)
    eval_json = json.dumps([{"id": 1, "title": "Alpha"}])
    captured_envs: list[dict | None] = []

    async def _run_capture(args, *, timeout=None, env=None):
        captured_envs.append(env)
        subcommand = args[2] if len(args) > 2 else None
        if subcommand == "eval":
            return BrowserActResult(returncode=0, stdout=eval_json, stderr="")
        return BrowserActResult(returncode=0, stdout="", stderr="")

    with patch("backend.database.AsyncSessionLocal", sm):
        await AuthManager().store("src-fetch-1", "browser_act_api_key", "fetch-path-key")

        with patch("backend.browser_act.cli._run", side_effect=_run_capture):
            ctx = FetchContext(
                config={"pack": PACK_SELECTOR, "params": {"keyword": "shoes"}, "mode": "stealth"},
                params={},
                source_id="src-fetch-1",
            )
            result = await channel.fetch(ctx)

    assert result.items == [{"id": 1, "title": "Alpha"}]
    assert any(
        env and env.get("BROWSER_ACT_API_KEY") == "fetch-path-key" for env in captured_envs
    )
