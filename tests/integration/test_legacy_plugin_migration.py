"""Regression coverage for databases previously run from plugin-hub."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _create_legacy_plugin_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        );
        INSERT INTO alembic_version VALUES ('u0z1a2b3c4d5');

        CREATE TABLE source_cursors (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            source_id VARCHAR(36) NOT NULL,
            cursor JSON NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        INSERT INTO source_cursors VALUES (
            'cursor-1', 'source-1', '{"offset": 7}',
            '2026-07-19 00:00:00', '2026-07-19 00:00:00'
        );

        CREATE TABLE collected_records (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            source_id VARCHAR(36) NOT NULL,
            workflow_id VARCHAR(255),
            workflow_run_id VARCHAR(36)
        );
        INSERT INTO collected_records VALUES (
            'record-1', 'source-1', 'workflow-1', 'run-1'
        );

        CREATE TABLE feed_providers (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            name VARCHAR(255) NOT NULL
        );
        """
    )
    connection.commit()
    connection.close()


def test_legacy_plugin_database_rejoins_current_migration_head(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-plugin.db"
    _create_legacy_plugin_database(database_path)
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database_path.as_posix()}",
    }

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    connection = sqlite3.connect(database_path)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        cursor_columns = {row[1] for row in connection.execute("PRAGMA table_info(source_cursors)")}
        record_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(collected_records)")
        }
        record_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(collected_records)")
        }
        cursor = connection.execute(
            "SELECT source_id, cursor, version FROM source_cursors WHERE id = 'cursor-1'"
        ).fetchone()
        record = connection.execute(
            "SELECT source_id, workflow_id, workflow_run_id, identity_key "
            "FROM collected_records WHERE id = 'record-1'"
        ).fetchone()
    finally:
        connection.close()

    assert revision == ("a8b9c0d1e2f3",)
    assert "version" in cursor_columns
    assert "identity_key" in record_columns
    assert "ix_collected_records_source_identity" in record_indexes
    assert cursor == ("source-1", '{"offset": 7}', 0)
    assert record == ("source-1", "workflow-1", "run-1", None)


def test_current_database_repairs_missing_plugin_installation_table(tmp_path: Path) -> None:
    database_path = tmp_path / "drifted-current.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        );
        INSERT INTO alembic_version VALUES ('f3g4h5i6j7k8');
        """
    )
    connection.close()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[2],
        env={
            **os.environ,
            "DATABASE_URL": f"sqlite+aiosqlite:///{database_path.as_posix()}",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    connection = sqlite3.connect(database_path)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'plugin_installations'"
        ).fetchone()
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(plugin_installations)")
        }
    finally:
        connection.close()

    assert revision == ("a8b9c0d1e2f3",)
    assert table == ("plugin_installations",)
    assert "ix_plugin_installations_provider_key" in indexes


def test_current_head_repairs_missing_record_identity_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "drifted-record-identity.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        );
        INSERT INTO alembic_version VALUES ('h5i6j7k8l9m0');

        CREATE TABLE collected_records (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            source_id VARCHAR(36) NOT NULL
        );
        INSERT INTO collected_records VALUES ('record-1', 'source-1');
        """
    )
    connection.close()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[2],
        env={
            **os.environ,
            "DATABASE_URL": f"sqlite+aiosqlite:///{database_path.as_posix()}",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    connection = sqlite3.connect(database_path)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(collected_records)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(collected_records)")}
        record = connection.execute(
            "SELECT source_id, identity_key FROM collected_records WHERE id = 'record-1'"
        ).fetchone()
    finally:
        connection.close()

    assert revision == ("a8b9c0d1e2f3",)
    assert "identity_key" in columns
    assert "ix_collected_records_source_identity" in indexes
    assert record == ("source-1", None)
