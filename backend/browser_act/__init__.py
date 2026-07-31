"""backend.browser_act — browser-act CLI subprocess wrapper (Browser Act integration PR-B).

Distinct from both:
- ``backend/cli.py`` — the opencli-skill HTTP client (unrelated tool).
- ``backend/browser_act_packs/`` — PR-A's vendored SKILL.md pack files.

This package only wraps the external ``browser-act`` binary as a subprocess
(``cli.py``). The manifest-driven channel that drives it (``collect()``,
pack manifests, needs_human handling) is PR-C's ``BrowserActChannel``.
"""

from backend.browser_act.cli import (
    BrowserActError,
    BrowserActResult,
    BrowserActSession,
    get_skills,
    session,
    version,
)

__all__ = [
    "BrowserActError",
    "BrowserActResult",
    "BrowserActSession",
    "get_skills",
    "session",
    "version",
]
