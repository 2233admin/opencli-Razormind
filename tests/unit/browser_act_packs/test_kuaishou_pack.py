"""Behavioral checks for the Kuaishou Browser Act pack."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.browser_act_packs.catalog import PackCatalog

_PACK = Path(PackCatalog().root) / "video-platforms" / "kuaishou-search"


def _extract(state: dict, max_results: int) -> dict:
    script = subprocess.run(
        [
            sys.executable,
            str(_PACK / "scripts" / "extract-search.py"),
            "--max-results",
            str(max_results),
        ],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    ).stdout
    result = subprocess.run(
        [
            "node",
            "-e",
            "const fs = require('node:fs');"
            "const {state, script} = JSON.parse(fs.readFileSync(0, 'utf8'));"
            "globalThis.window = {INIT_STATE: state};"
            "console.log(JSON.stringify(eval(script)));",
        ],
        input=json.dumps({"state": state, "script": script}, ensure_ascii=False),
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    return json.loads(result.stdout)


def test_search_returns_bounded_records_after_skipping_invalid_feeds() -> None:
    result = _extract(
        {
            "search": {
                "feeds": [
                    None,
                    {"photo": {"caption": "Missing ID"}},
                    {
                        "photo": {
                            "id": "a/b",
                            "caption": "  快手\n 视频  ",
                            "timestamp": 1700000000,
                            "likeCount": 0,
                            "coverUrl": [{"url": "https://media.example/cover.jpg"}],
                            "manifest": {"playUrl": "https://media.example/video.mp4"},
                        },
                        "author": {"name": "  Alice  "},
                    },
                    {"photo": {"id": "second"}},
                    {"photo": {"id": "third"}},
                ]
            }
        },
        2,
    )

    assert result["count"] == 2
    assert [item["url"] for item in result["items"]] == [
        "https://www.kuaishou.com/short-video/a%2Fb",
        "https://www.kuaishou.com/short-video/second",
    ]
    first = result["items"][0]
    assert first["title"] == "快手 视频"
    assert first["author"] == "Alice"
    assert first["published_at"] == "2023-11-14T22:13:20.000Z"
    assert first["statistics"] == {"like_count": 0}
    assert first["cover_url"] == "https://media.example/cover.jpg"
    assert first["play_url"] == "https://media.example/video.mp4"


@pytest.mark.parametrize(("requested", "expected"), [(0, 1), (100, 50)])
def test_search_clamps_result_limit(requested: int, expected: int) -> None:
    feeds = [{"photo": {"id": str(index)}} for index in range(51)]

    result = _extract({"search": {"feeds": feeds}}, requested)

    assert result["count"] == expected
    assert [item["photo_id"] for item in result["items"]] == [
        str(index) for index in range(expected)
    ]


def test_search_without_initial_state_returns_no_records() -> None:
    assert _extract({}, 10) == {"count": 0, "items": []}
