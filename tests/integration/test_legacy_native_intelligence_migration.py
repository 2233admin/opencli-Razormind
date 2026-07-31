"""Regression coverage for legacy plugin databases joining the native runtime head."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]


def _run_alembic(
    database_url: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_legacy_plugin_head_rejoins_native_intelligence_head(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-native-intelligence.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"

    baseline = _run_alembic(database_url, "upgrade", "u0a1b2c3d4e5")
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE legacy_workspace_marker (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                name VARCHAR(255) NOT NULL
            );
            INSERT INTO legacy_workspace_marker VALUES (
                'workspace-1',
                'native-intelligence-workspace'
            );
            UPDATE alembic_version SET version_num = 'z5f6g7h8i9j0';
            """
        )

    upgraded = _run_alembic(database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

    with sqlite3.connect(database_path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        marker = connection.execute(
            "SELECT id, name FROM legacy_workspace_marker"
        ).fetchone()
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert revision == ("k8l9m0n1o2p3",)
    assert marker == ("workspace-1", "native-intelligence-workspace")
    assert "intelligence_sessions" in tables
    assert "intelligence_artifacts" in tables
