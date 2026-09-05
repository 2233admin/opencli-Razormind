"""Fresh and legacy materialization-index migrations converge on one canonical name."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.postgres_conformance import temporary_postgres_database

ROOT = Path(__file__).parents[2]
LEGACY_INDEX = "ix_evidence_batch_materialization_manifests_materialization_sta"
CANONICAL_INDEX = "ix_evidence_batch_materialization_status"
PRE_CONVERGENCE_HEAD = "g5b6c7d8e9f0"


def _upgrade(database_url: str, revision: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_materialization_index_migration_is_a_sqlite_noop_with_a_fresh_canonical_index(
    tmp_path: Path,
):
    database = tmp_path / "materialization.db"
    result = _upgrade(f"sqlite+aiosqlite:///{database.as_posix()}", "head")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list('evidence_batch_materialization_manifests')"
            )
        }
    assert CANONICAL_INDEX in indexes
    assert LEGACY_INDEX not in indexes


async def _index_names(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.scalars(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = 'evidence_batch_materialization_manifests'"
                )
            )
            return {str(name) for name in rows}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres_conformance
async def test_postgresql_materialization_index_legacy_and_fresh_paths_converge():
    async with temporary_postgres_database("materialization_index_legacy") as legacy_url:
        before = await asyncio.to_thread(_upgrade, legacy_url, PRE_CONVERGENCE_HEAD)
        assert before.returncode == 0, before.stderr
        engine = create_async_engine(legacy_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(f"ALTER INDEX {CANONICAL_INDEX} RENAME TO {LEGACY_INDEX}")
                )
        finally:
            await engine.dispose()
        repaired = await asyncio.to_thread(_upgrade, legacy_url, "head")
        assert repaired.returncode == 0, repaired.stderr
        assert CANONICAL_INDEX in await _index_names(legacy_url)
        assert LEGACY_INDEX not in await _index_names(legacy_url)

    async with temporary_postgres_database("materialization_index_fresh") as fresh_url:
        fresh = await asyncio.to_thread(_upgrade, fresh_url, "head")
        assert fresh.returncode == 0, fresh.stderr
        assert CANONICAL_INDEX in await _index_names(fresh_url)
        assert LEGACY_INDEX not in await _index_names(fresh_url)
