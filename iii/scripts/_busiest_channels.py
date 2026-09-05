"""Find busiest Discord channels overall (not keyword-filtered)."""
from __future__ import annotations

import json
import subprocess
from collections import Counter

proc = subprocess.run(["discord", "stats", "--json"], capture_output=True, text=True, check=True)
channels = json.loads(proc.stdout).get("data", {}).get("channels", [])
channels.sort(key=lambda c: int(c.get("msg_count") or 0), reverse=True)

print("=== Top 40 channels by cached msg_count (ALL guilds) ===\n")
print(f"{'msgs':>4}  {'last':<10}  {'channel_id':<20}  guild / channel")
print("-" * 100)
for ch in channels[:40]:
    print(
        f"{int(ch.get('msg_count') or 0):>4}  "
        f"{str(ch.get('last_msg', ''))[:10]:<10}  "
        f"{ch['channel_id']:<20}  "
        f"{ch.get('guild_name', '?')}/{ch.get('channel_name', '?')}"
    )

# Guild totals
by_guild: Counter[str] = Counter()
guild_names: dict[str, str] = {}
for ch in channels:
    gid = str(ch.get("guild_id") or ch.get("guild_name") or "?")
    gname = str(ch.get("guild_name") or "?")
    guild_names[gid] = gname
    by_guild[gname] += int(ch.get("msg_count") or 0)

print("\n=== Top guilds by total cached messages ===\n")
for gname, total in by_guild.most_common(20):
    n_ch = sum(1 for c in channels if c.get("guild_name") == gname)
    print(f"{total:>6} msgs  {n_ch:>3} ch  {gname}")

# today
proc2 = subprocess.run(["discord", "today", "--json"], capture_output=True, text=True)
if proc2.returncode == 0:
    try:
        today = json.loads(proc2.stdout)
        if isinstance(today, dict):
            groups = today.get("channels") or today.get("data") or today
        else:
            groups = today
        print("\n=== discord today (if any) ===")
        print(json.dumps(groups, ensure_ascii=False, indent=2)[:3000])
    except json.JSONDecodeError:
        print("\n=== discord today (raw) ===")
        print(proc2.stdout[:2000])
else:
    print("\n(today:", proc2.stderr.strip()[:200], ")")

# top senders
proc3 = subprocess.run(["discord", "top", "--json"], capture_output=True, text=True)
if proc3.returncode == 0:
    try:
        top = json.loads(proc3.stdout)
        print("\n=== top senders (sample) ===")
        print(json.dumps(top, ensure_ascii=False, indent=2)[:2500])
    except json.JSONDecodeError:
        print(proc3.stdout[:1500])
