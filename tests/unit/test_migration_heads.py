import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from backend.config import get_settings


def test_alembic_has_one_head():
    config = Config()
    config.set_main_option("script_location", "backend/migrations")

    assert ScriptDirectory.from_config(config).get_heads() == ["a8b9c0d1e2f3"]


def test_upgrade_head_creates_identity_and_operations_tables(monkeypatch):
    with TemporaryDirectory() as directory:
        database = Path(directory) / "migration.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
        get_settings.cache_clear()
        config = Config()
        config.set_main_option("script_location", "backend/migrations")

        try:
            command.upgrade(config, "head")
        finally:
            get_settings.cache_clear()

        connection = sqlite3.connect(database)
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            connection.close()

    assert {
        "users",
        "workspace_memberships",
        "operations_work_items",
        "operations_agent_identities",
        "agent_permission_profiles",
        "operations_agent_drafts",
        "published_operations_agent_versions",
        "operations_agent_runs",
        "consumer_grants",
        "automations",
        "projects",
        "workflows",
        "workflow_drafts",
        "workflow_versions",
    } <= tables


def test_workflow_run_version_foreign_key_is_restrict(monkeypatch):
    with TemporaryDirectory() as directory:
        database = Path(directory) / "migration.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
        get_settings.cache_clear()
        config = Config()
        config.set_main_option("script_location", "backend/migrations")

        try:
            command.upgrade(config, "head")
        finally:
            get_settings.cache_clear()

        connection = sqlite3.connect(database)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_runs)")}
            foreign_keys = list(connection.execute("PRAGMA foreign_key_list(workflow_runs)"))
        finally:
            connection.close()

    assert "workflow_version_id" in columns
    assert any(
        row[2] == "workflow_versions"
        and row[3] == "workflow_version_id"
        and row[4] == "id"
        and row[6] == "RESTRICT"
        for row in foreign_keys
    )
    assert "studio_workflow_version_id" in columns
    assert any(
        row[2] == "studio_workflow_versions"
        and row[3] == "studio_workflow_version_id"
        and row[4] == "id"
        and row[6] == "RESTRICT"
        for row in foreign_keys
    )


def test_c2_downgrade_removes_only_operations_agent_tables(monkeypatch):
    with TemporaryDirectory() as directory:
        database = Path(directory) / "migration.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
        get_settings.cache_clear()
        config = Config()
        config.set_main_option("script_location", "backend/migrations")

        try:
            command.upgrade(config, "head")
            command.downgrade(config, "b9c0d1e2f3a4")
        finally:
            get_settings.cache_clear()

        connection = sqlite3.connect(database)
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            connection.close()

    assert "workspaces" in tables
    assert (
        not {
            "operations_agent_identities",
            "agent_permission_profiles",
            "operations_agent_drafts",
            "published_operations_agent_versions",
            "operations_agent_runs",
        }
        & tables
    )
