"""Probe 观察者聚合: parse channels, sync chat/feed channels, rank by activity."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

GUILD_ID = "1340918593067679799"
CHANNELS_DUMP = Path(r"C:\Users\Administrator\agent-tools\95c990a4-1eeb-4489-bd38-3997f362f680.txt")

# Priority tiers for 观察者聚合
TIER_A = ("社区交流群", "内部群", "公告群", "盈亏分享", "博主战绩", "精华汇总")
TIER_B = ("推特", "视频", "订阅", "策略", "警报", "战绩", "跟单", "分析")


def parse_yaml_channels(text: str) -> list[dict]:
    channels = []
    cur: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- id:"):
            if cur:
                channels.append(cur)
            cur = {"id": s.split(":", 1)[1].strip().strip("'\"")}
        elif cur is not None:
            if s.startswith("name:"):
                cur["name"] = s.split(":", 1)[1].strip()
            elif s.startswith("type:"):
                cur["type"] = int(s.split(":", 1)[1].strip())
            elif s.startswith("parent_id:"):
                val = s.split(":", 1)[1].strip()
                cur["parent_id"] = None if val == "null" else val.strip("'\"")
    if cur:
        channels.append(cur)
    return channels


def tier(name: str) -> int:
    if any(k in name for k in TIER_A):
        return 0
    if any(k in name for k in TIER_B):
        return 1
    return 2


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> None:
    text = CHANNELS_DUMP.read_text(encoding="utf-8")
    all_ch = [c for c in parse_yaml_channels(text) if c.get("type") == 0]
    print(f"观察者聚合 text channels: {len(all_ch)}")

    # Show tier A/B names first
    ranked = sorted(all_ch, key=lambda c: (tier(c.get("name", "")), c.get("name", "")))
    print("\n=== Tier A — 群聊 / 社区（优先 sync）===\n")
    tier_a = [c for c in ranked if tier(c["name"]) == 0]
    for c in tier_a:
        print(f"  {c['id']}  {c['name']}")

    print(
        f"\n=== Tier B — 转发流 / 策略订阅（共 "
        f"{sum(1 for c in ranked if tier(c['name']) == 1)} 个，列前 30）===\n"
    )
    tier_b = [c for c in ranked if tier(c["name"]) == 1][:30]
    for c in tier_b:
        print(f"  {c['id']}  {c['name']}")

    # Sync tier A + first 15 tier B
    to_sync = tier_a + [c for c in ranked if tier(c["name"]) == 1][15:30]
    print(f"\n=== Syncing {len(to_sync)} channels ===\n")
    for c in to_sync:
        print(f"sync {c['name'][:40]}...", flush=True)
        run(["discord", "dc", "sync", c["id"]])
        time.sleep(0.35)

    stats = json.loads(run(["discord", "stats", "--json"]).stdout)
    by_id = {str(x["channel_id"]): x for x in stats["data"]["channels"]}

    print("\n=== Activity after sync (msg_count, last_msg) ===\n")
    rows = []
    for c in to_sync:
        ch = by_id.get(c["id"], {})
        mc = int(ch.get("msg_count") or 0)
        last = str(ch.get("last_msg", ""))[:10]
        rows.append((mc, last, c["id"], c["name"]))
    rows.sort(reverse=True)
    for mc, last, cid, name in rows:
        print(f"{mc:>4}  {last}  {cid}  {name}")


if __name__ == "__main__":
    main()
