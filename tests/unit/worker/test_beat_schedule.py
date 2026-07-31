"""Unit tests for backend/worker/beat_schedule.py — the surviving
parse_cron_expression helper (its former build_beat_schedule() was dead code,
removed in runtime-hardening phase PR-C, superseded by worker/redbeat_sync.py)."""

import pytest
from celery.schedules import crontab

from backend.worker.beat_schedule import parse_cron_expression


def test_parse_cron_expression_valid():
    result = parse_cron_expression("*/5 9 * * 1-5")
    assert isinstance(result, crontab)


def test_parse_cron_expression_wrong_field_count():
    with pytest.raises(ValueError, match="need 5 fields"):
        parse_cron_expression("* * * *")
