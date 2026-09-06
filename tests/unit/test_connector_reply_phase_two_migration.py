import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


def _run(repository: Path, database: Path, *args: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_connector_reply_phase_two_migration_round_trip(tmp_path):
    database = tmp_path / "connector-p2-migration.db"
    repository = Path(__file__).parents[2]
    _run(repository, database, "upgrade", "cr125p2a")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    inspector = inspect(engine)
    assert {
        "connector_reply_grants",
        "connector_outbound_deliveries",
        "connector_artifact_grants",
    } <= set(inspector.get_table_names())
    receipt_columns = {
        column["name"] for column in inspector.get_columns("connector_inbound_receipts")
    }
    assert {
        "artifact_grant_id",
        "turn_id",
        "outbound_delivery_id",
        "lease_owner",
        "lease_generation",
        "grant_lease_generation",
        "lease_expires_at",
        "processing_started_at",
    } <= receipt_columns
    indexes = {index["name"] for index in inspector.get_indexes("connector_inbound_receipts")}
    assert "ix_connector_receipt_recovery" in indexes
    engine.dispose()

    _run(repository, database, "downgrade", "cr125w1a")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    inspector = inspect(engine)
    assert "connector_reply_grants" not in inspector.get_table_names()
    receipt_columns = {
        column["name"] for column in inspector.get_columns("connector_inbound_receipts")
    }
    assert "lease_generation" not in receipt_columns
    engine.dispose()
