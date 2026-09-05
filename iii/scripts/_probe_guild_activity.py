"""Probe crypto guilds not yet in local cache — list channels + sync + count."""
from __future__ import annotations

import json
import subprocess
import time

GUILDS = [
    ("1340918593067679799", "观察者聚合"),
    ("1138342273285890069", "STARX 恒星商学院"),
    ("1220284495794540597", "笨錢社群"),
    ("1417859743472685098", "铁律交易"),
    ("1143938900688125973", "BigBeluga"),
    ("1082583892877385759", "AlgoAlpha"),
    ("1381644986726088725", "LTC"),
    ("1404462210679312416", "BOSWaves"),
    ("1219847221055455263", "openalgo"),
    ("1140122601746862181", "AI4Finance"),
    ("897744850118639636", "Yuchi Trader"),
]

CHAT_HINTS = (
    "general", "chat", "交流", "讨论", "hangout", "crypto", "trading", "trade",
    "分享", "转发", "feed", "signal", "群友", "闲聊", "off-topic", "social",
    "π货", "爆料", "观察", "聚合", "news", "profits", "profit",
)


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else p.stderr


def parse_channels_yamlish(text: str) -> list[dict]:
    """Parse discord dc channels yaml output roughly."""
    channels = []
    cur: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- id:"):
            if cur:
                channels.append(cur)
            cur = {"id": s.split(":", 1)[1].strip().strip("'\"")}
        elif cur is not None and s.startswith("name:"):
            cur["name"] = s.split(":", 1)[1].strip()
        elif cur is not None and s.startswith("type:"):
            cur["type"] = int(s.split(":", 1)[1].strip())
    if cur:
        channels.append(cur)
    return channels


def score_chat(name: str) -> int:
    n = name.lower()
    return sum(1 for h in CHAT_HINTS if h.lower() in n)


def main() -> None:
    stats_raw = run(["discord", "stats", "--json"])
    stats_map = {}
    if stats_raw.strip().startswith("{"):
        for ch in json.loads(stats_raw).get("data", {}).get("channels", []):
            stats_map[str(ch["channel_id"])] = int(ch.get("msg_count") or 0)

    print("=== Crypto guilds: chat-like channels (type 0 text) ===\n")
    candidates: list[tuple[int, str, str, str, str]] = []

    for gid, gname in GUILDS:
        out = run(["discord", "dc", "channels", gid])
        chs = [c for c in parse_channels_yamlish(out) if c.get("type") == 0]
        chat_like = sorted(chs, key=lambda c: -score_chat(c.get("name", "")))
        print(f"## {gname} ({gid}) — {len(chs)} text channels")
        shown = 0
        for c in chat_like:
            if score_chat(c.get("name", "")) == 0 and shown >= 8:
                continue
            cid = c["id"]
            cached = stats_map.get(cid, 0)
            sc = score_chat(c.get("name", ""))
            line = f"  [{cached:>4} cached] {cid}  {c.get('name','')}"
            if sc:
                line += "  *"
            print(line)
            shown += 1
            if sc or "general" in c.get("name", "").lower() or "chat" in c.get("name", "").lower():
                candidates.append((sc, gid, gname, cid, c.get("name", "")))
        print()

    # Sync top candidates (max 12) and re-read stats
    candidates.sort(key=lambda x: -x[0])
    to_sync = candidates[:12]
    print("=== Syncing top chat-like candidates ===\n")
    for _, gid, gname, cid, name in to_sync:
        run(["discord", "dc", "sync", cid])
        time.sleep(0.4)

    stats_raw2 = run(["discord", "stats", "--json"])
    stats_map2 = {}
    if stats_raw2.strip().startswith("{"):
        for ch in json.loads(stats_raw2).get("data", {}).get("channels", []):
            stats_map2[str(ch["channel_id"])] = ch

    print("=== After sync (by msg_count) ===\n")
    rows = []
    for _, gid, gname, cid, name in to_sync:
        ch = stats_map2.get(cid, {})
        mc = int(ch.get("msg_count") or 0) if ch else 0
        last = str(ch.get("last_msg", ""))[:10] if ch else ""
        rows.append((mc, gname, name, cid, last))
    rows.sort(reverse=True)
    for mc, gname, name, cid, last in rows:
        print(f"{mc:>4}  {cid}  {gname} / {name}  last={last}")


if __name__ == "__main__":
    main()
