import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from backend.config import get_settings


def _config(database: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "backend" / "migrations")
    )
    return config


def _old_rows(connection: sqlite3.Connection) -> tuple[tuple, tuple, tuple, tuple, tuple]:
    now = "2026-09-06 00:00:00"
    connection.execute(
        "INSERT INTO agent_conversations "
        "(id, created_at, updated_at, workspace_id, title, status, created_by_user_id, "
        "context_binding, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("conversation", now, now, "workspace", "Preserved", "active", "user", "{}", 3),
    )
    connection.execute(
        "INSERT INTO agent_conversation_turns "
        "(id, created_at, updated_at, conversation_id, workspace_id, sequence, request_id, "
        "user_content, response, context_binding, tool_trace, status, error_code, error_message) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "turn",
            now,
            now,
            "conversation",
            "workspace",
            1,
            "request",
            "hello",
            json.dumps({"type": "message", "content": "world"}),
            "{}",
            "[]",
            "completed",
            None,
            None,
        ),
    )
    connection.execute(
        "INSERT INTO agent_sessions "
        "(id, created_at, updated_at, workspace_id, actor_subject, context) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("session", now, now, "workspace", "actor", "{}"),
    )
    connection.execute(
        "INSERT INTO agent_runs "
        "(id, created_at, updated_at, session_id, kind, status, goal, request_payload, "
        "reply_payload, error_message, next_event_sequence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "run",
            now,
            now,
            "session",
            "chat",
            "completed",
            "goal",
            "{}",
            json.dumps({"content": "done"}),
            None,
            2,
        ),
    )
    connection.execute(
        "INSERT INTO agent_run_events "
        "(id, created_at, updated_at, run_id, sequence, event_type, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("event", now, now, "run", 1, "reply", json.dumps({"state": "completed"})),
    )
    connection.commit()
    return tuple(
        connection.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        for table, row_id in (
            ("agent_conversations", "conversation"),
            ("agent_conversation_turns", "turn"),
            ("agent_sessions", "session"),
            ("agent_runs", "run"),
            ("agent_run_events", "event"),
        )
    )


def test_conversation_run_migration_preserves_old_rows_round_trip(tmp_path, monkeypatch):
    database = tmp_path / "conversation-runs.db"
    config = _config(database, monkeypatch)
    try:
        command.upgrade(config, "cr125p2a")
        connection = sqlite3.connect(database)
        before = _old_rows(connection)
        connection.close()

        command.upgrade(config, "ac126run1")
        command.downgrade(config, "cr125p2a")

        connection = sqlite3.connect(database)
        after = tuple(
            connection.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
            for table, row_id in (
                ("agent_conversations", "conversation"),
                ("agent_conversation_turns", "turn"),
                ("agent_sessions", "session"),
                ("agent_runs", "run"),
                ("agent_run_events", "event"),
            )
        )
        connection.close()
        assert after == before
    finally:
        get_settings.cache_clear()


def test_conversation_run_downgrade_refuses_new_status_before_schema_change(tmp_path, monkeypatch):
    database = tmp_path / "conversation-runs-guard.db"
    config = _config(database, monkeypatch)
    try:
        command.upgrade(config, "ac126run1")
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys = OFF")
        now = "2026-09-06 00:00:00"
        connection.execute(
            "INSERT INTO agent_conversation_turns "
            "(id, created_at, updated_at, conversation_id, workspace_id, sequence, request_id, "
            "active_slot, agent_run_id, user_content, response, context_binding, tool_trace, "
            "status, "
            "error_code, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "queued-turn",
                now,
                now,
                "conversation",
                "workspace",
                1,
                "queued",
                "conversation",
                None,
                "hello",
                None,
                "{}",
                "[]",
                "queued",
                None,
                None,
            ),
        )
        connection.commit()
        connection.close()

        with pytest.raises(RuntimeError, match="queued or interrupted"):
            command.downgrade(config, "cr125p2a")

        connection = sqlite3.connect(database)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(agent_conversation_turns)")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        connection.close()
        assert {"active_slot", "agent_run_id"} <= columns
        assert revision == "ac126run1"
    finally:
        get_settings.cache_clear()
