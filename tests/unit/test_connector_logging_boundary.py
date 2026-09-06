"""Production database logging must not expose connector payload parameters."""

import os
import subprocess
import sys
from pathlib import Path


def test_debug_database_logging_hides_payloads_and_exception_parameters(tmp_path):
    repository = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = (
        f"sqlite+aiosqlite:///{(tmp_path / 'connector-log-probe.db').as_posix()}"
    )
    environment["DEBUG"] = "true"
    marker = "connector-private-payload-marker-7319"
    program = """
import asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from backend.database import engine

async def main():
    try:
        async with engine.begin() as connection:
            await connection.execute(text(
                "CREATE TABLE connector_log_probe (id INTEGER PRIMARY KEY, payload TEXT)"
            ))
            statement = text(
                "INSERT INTO connector_log_probe (id, payload) VALUES (:id, :payload)"
            )
            parameters = {"id": 1, "payload": "connector-private-payload-marker-7319"}
            await connection.execute(statement, parameters)
            try:
                await connection.execute(statement, parameters)
            except IntegrityError as error:
                print(str(error))
            else:
                raise AssertionError("duplicate key must exercise exception formatting")
    finally:
        await engine.dispose()
    print("connector SQL logging probe completed")

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "connector SQL logging probe completed" in output
    assert "INSERT INTO connector_log_probe" in output
    assert "UNIQUE constraint failed" in output
    assert marker not in output
    assert "SQL parameters hidden" in output
