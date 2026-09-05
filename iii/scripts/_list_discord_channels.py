"""One-off helper: list Discord channels from discord-cli stats."""

import json
import subprocess

proc = subprocess.run(["discord", "stats", "--json"], capture_output=True, text=True, check=True)
data = json.loads(proc.stdout)
channels = data.get("data", {}).get("channels", [])

keywords = (
    "hummingbot",
    "openalgo",
    "algo",
    "finance",
    "quant",
    "trading",
    "crypto",
    "ai4",
    "观察",
    "gsd",
    "agent",
    "mem",
    "ltc",
    "starx",
    "bigbeluga",
    "method",
    "tauric",
    "gitnexus",
    "ultrawork",
    "paperclip",
)

print("=== ODP-relevant channels (by msg_count) ===")
matched = []
for ch in channels:
    blob = f"{ch.get('guild_name','')} {ch.get('channel_name','')}".lower()
    if any(k in blob for k in keywords):
        matched.append(ch)
matched.sort(key=lambda c: int(c.get("msg_count") or 0), reverse=True)
for ch in matched[:30]:
    print(
        f"{ch.get('msg_count',0):>4}  {ch['channel_id']}  "
        f"{ch.get('guild_name','?')}/{ch.get('channel_name','?')}"
    )

print("\n=== Recently active (last_msg in 2026) top 20 ===")
recent = [c for c in channels if str(c.get("last_msg", "")).startswith("2026")]
recent.sort(key=lambda c: c.get("last_msg", ""), reverse=True)
for ch in recent[:20]:
    print(
        f"{ch.get('msg_count', 0):>4}  {ch['channel_id']}  "
        f"{ch.get('guild_name', '?')}/{ch.get('channel_name', '?')}  "
        f"last={ch.get('last_msg', '')[:10]}"
    )
