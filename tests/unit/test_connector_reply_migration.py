import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


def test_connector_foundation_migration_upgrades_temporary_database(tmp_path):
    database = tmp_path / "migration.db"
    repository = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "cr125w1a"],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    tables = set(inspect(create_engine(f"sqlite:///{database.as_posix()}")).get_table_names())
    assert {
        "connector_installations",
        "connector_binding_challenges",
        "connector_principal_bindings",
        "connector_inbound_receipts",
    } <= tables
