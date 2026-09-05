"""Sync + sample crypto channels for forward/embed ratio."""
from __future__ import annotations

import json
import re
import subprocess
import time

# High-msg crypto channels likely forward-heavy (from stats + channel names)
CANDIDATES = [
    ("1372977553614180362", "CryptoTraders.com", "『🐣』ᴛᴡɪᴛᴛᴇʀ"),
    ("990356588055379999", "CryptoTraders.com", "『🤑』𝗣𝗥𝗢𝗙𝗜𝗧𝗦"),
    ("990356439199514664", "CryptoTraders.com", "『📢』ᴀɴɴᴏᴜɴᴄᴇᴍᴇɴᴛꜱ"),
    ("1033204559880925305", "Yuchi Trader", "📊tradingview工具"),
    ("897744850118639639", "Yuchi Trader", "🎬｜youtube视频"),
    ("959220727364612106", "Yuchi Trader", "📎futures-news"),
    ("1087585066877722734", "Yuchi Trader", "💹策略分享"),
    ("1372991193285267568", "CryptoTraders.com", "『🧾』ᴄᴏᴍᴍᴀɴᴅꜱ"),
]

FORWARD_PATTERNS = (
    re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/", re.I),
    re.compile(r"https?://(?:www\.)?t\.me/", re.I),
    re.compile(r"https?://(?:www\.)?tradingview\.com/", re.I),
    re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", re.I),
)


def msg_blob(m: dict) -> str:
    parts = [str(m.get("content") or "")]
    for emb in m.get("embeds") or []:
        if isinstance(emb, dict):
            for k in ("title", "description", "url"):
                parts.append(str(emb.get(k) or ""))
    return " ".join(parts)


def is_forward(m: dict) -> bool:
    if m.get("embeds") or m.get("attachments"):
        return True
    text = msg_blob(m)
    return any(p.search(text) for p in FORWARD_PATTERNS)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> None:
    print("Syncing candidates (incremental)...")
    for cid, guild, name in CANDIDATES:
        run(["discord", "dc", "sync", cid])
        time.sleep(0.3)

    proc = run(["discord", "recent", "--json", "-n", "800"])
    if proc.returncode != 0:
        print("recent failed:", proc.stderr)
        return
    recent = json.loads(proc.stdout)
    if isinstance(recent, dict):
        recent = recent.get("data") or recent.get("messages") or []

    by_id: dict[str, list] = {}
    for m in recent:
        if isinstance(m, dict):
            by_id.setdefault(str(m.get("channel_id")), []).append(m)

    print("\n=== Per-channel forward ratio (from recent pool after sync) ===\n")
    print(f"{'fwd%':>5}  {'n':>3}  {'msgs':>4}  channel_id            guild / channel")
    print("-" * 95)

    for cid, guild, name in CANDIDATES:
        msgs = by_id.get(cid, [])
        n = len(msgs)
        fwd = sum(1 for m in msgs if is_forward(m))
        pct = (100.0 * fwd / n) if n else 0.0
        stats = run(["discord", "stats", "--json"])
        mc = "?"
        if stats.returncode == 0:
            data = json.loads(stats.stdout).get("data", {}).get("channels", [])
            for ch in data:
                if str(ch.get("channel_id")) == cid:
                    mc = str(ch.get("msg_count", "?"))
                    break
        name_l = name.lower()
        forward_name = any(
            x in name_l or x in name
            for x in ("twitter", "ᴛᴡɪᴛᴛᴇʀ", "youtube", "news", "tradingview", "𝗣𝗥𝗢𝗙𝗜𝗧", "profit")
        )
        tag = "  ← 转发为主" if (n >= 3 and pct >= 50) or forward_name else ""
        print(f"{pct:>4.0f}%  {n:>3}  {mc:>4}  {cid}  {guild}/{name}{tag}")

        # show 1 sample
        if msgs:
            sample = msgs[0]
            content = (sample.get("content") or "")[:80].replace("\n", " ")
            emb = len(sample.get("embeds") or [])
            print(f"       sample: emb={emb} | {content or '(empty)'}")


if __name__ == "__main__":
    main()
