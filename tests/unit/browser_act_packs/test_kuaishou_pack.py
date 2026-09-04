"""Contract checks for the Kuaishou Browser Act pack."""

import subprocess
import sys
from pathlib import Path

from backend.browser_act_packs.catalog import PackCatalog
from backend.browser_act_packs.manifest import load_manifest

_PACK = Path(PackCatalog().root) / "video-platforms" / "kuaishou-search"


def test_kuaishou_manifest_points_to_bounded_search_script() -> None:
    manifest = load_manifest(_PACK / "channel.manifest.json")

    assert manifest.domain == "video-platforms"
    assert manifest.capability == "kuaishou-search"
    assert manifest.success.required_field == "url"
    assert manifest.pagination.mode == "none"
    assert manifest.steps[-1].script == "scripts/extract-search.py"
    assert (_PACK / manifest.steps[-1].script).is_file()


def test_kuaishou_script_emits_requested_result_bound() -> None:
    script = _PACK / "scripts" / "extract-search.py"
    result = subprocess.run(
        [sys.executable, str(script), "--max-results", "7"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "MAX_RESULTS" not in result.stdout
    assert ".slice(0, 7)" in result.stdout
    assert "kuaishou.com/short-video" in result.stdout
