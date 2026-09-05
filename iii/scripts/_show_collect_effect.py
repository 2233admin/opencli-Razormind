"""Show what Discord collection actually captures (messages → Record v2)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

III_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(III_ROOT))

from lib.discord_cli import recent_messages, sync_channel  # noqa: E402
from lib.odp_record import discord_messages_to_events, source_id_for_channel  # noqa: E402

CHANNELS = [
    ("1357021092023111770", "社区交流群", "guanzhe-community"),
    ("1410565827975053363", "视频通知", "guanzhe-video"),
    ("1356701964141985952", "woods", "guanzhe-woods"),
]


def run_iii_trigger(schedule_id: str) -> dict:
    iii = Path.home() / ".local" / "iii" / "iii.exe"
    proc = subprocess.run(
        [str(iii), "trigger", f"odp.schedule::discord/{schedule_id}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr or proc.stdout}
    return json.loads(proc.stdout)


def preview_channel(channel_id: str, channel_filter: str, limit: int = 20) -> None:
    sync_channel(channel_id)
    msgs = recent_messages(limit=limit, channel_name=channel_filter)
    matched = [m for m in msgs if str(m.get("channel_id") or "") == channel_id]
    if not matched and msgs:
        matched = msgs
    events = discord_messages_to_events(
        matched[:limit],
        source_id=source_id_for_channel(channel_id),
        task_id="preview-task",
        trace_id="preview-trace",
    )
    print(f"\n{'='*72}")
    print(f"channel_id={channel_id}  filter={channel_filter!r}")
    print(f"recent={len(msgs)}  matched={len(matched)}  events={len(events)}")
    print(f"source_id={source_id_for_channel(channel_id)}")
    print("-" * 72)
    for m in matched[:6]:
        ts = str(m.get("timestamp") or "")[:16]
        sender = m.get("sender_name") or m.get("sender_id") or "?"
        content = (m.get("content") or "").replace("\n", " ")[:100]
        att = len(m.get("attachments") or [])
        extra = f" +{att}附件" if att else ""
        print(f"  {ts}  {sender}: {content}{extra}")
    if events:
        ev = events[0]
        print("-" * 72)
        print("Record v2 sample (first event):")
        print(json.dumps(ev, ensure_ascii=False, indent=2)[:1200])


def main() -> None:
    print("=== III cron tick (ingest stats) ===")
    for _, _, sid in CHANNELS[:2]:
        r = run_iii_trigger(sid)
        ing = r.get("ingest") or {}
        print(
            f"  {sid}: matched={r.get('messages_matched')} "
            f"accepted={ing.get('accepted')} dup={ing.get('duplicates')} "
            f"rejected={ing.get('rejected')}"
        )

    print("\n=== Message preview (discord-cli, channel-scoped recent) ===")
    for cid, name_filter, _ in CHANNELS:
        preview_channel(cid, name_filter)


if __name__ == "__main__":
    main()
