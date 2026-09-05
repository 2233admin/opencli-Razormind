import re
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

    assert ScriptDirectory.from_config(config).get_heads() == ["int20260905a"]


def test_ci_downgrade_target_is_unambiguous():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    target = re.search(r"alembic downgrade (\S+)", workflow)
    assert target is not None

    config = Config()
    config.set_main_option("script_location", "backend/migrations")
    script = ScriptDirectory.from_config(config)

    assert script._downgrade_revs(target.group(1), tuple(script.get_heads()))


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
        "iii_collection_commands",
        "iii_collection_attempts",
        "iii_collection_outbox",
        "iii_collection_lifecycle_observations",
        "analysis_snapshot_receipts",
    } <= tables


def test_analysis_snapshot_migration_adds_typed_correlation_and_receipts(monkeypatch):
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
            acquisition_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(acquisition_executions)"
                )
            }
            acquisition_indexes = {
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list(acquisition_executions)"
                )
            }
            acquisition_table_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'acquisition_executions'"
            ).fetchone()[0]
            receipt_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(analysis_snapshot_receipts)"
                )
            }
            receipt_indexes = {
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list(analysis_snapshot_receipts)"
                )
            }
        finally:
            connection.close()

    assert {"workspace_id", "project_id", "workflow_id", "run_id"} <= acquisition_columns
    assert "ix_acquisition_executions_analysis_scope_started_at" in acquisition_indexes
    assert "ck_acquisition_executions_complete_run_correlation" in acquisition_table_sql
    assert {
        "selection_hash",
        "workspace_id",
        "project_id",
        "workflow_id",
        "studio_workflow_version_id",
        "run_id",
        "requested_by_user_id",
        "schema_version",
        "redaction_version",
        "source_start_at",
        "source_end_at",
        "workflow_trace_event_count",
        "acquisition_execution_metric_count",
        "total_row_count",
        "status",
        "failure_code",
        "attempt_count",
        "last_attempt_at",
        "completed_at",
        "expires_at",
    } <= receipt_columns
    assert "ix_analysis_snapshot_scope_created" in receipt_indexes


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
