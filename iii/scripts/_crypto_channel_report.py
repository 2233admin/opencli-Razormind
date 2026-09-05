"""List crypto-related Discord channels and estimate forward-heavy activity."""
from __future__ import annotations

import json
import re
import subprocess

CRYPTO_KW = (
    "crypto",
    "trader",
    "trade",
    "trading",
    "bitcoin",
    "btc",
    "eth",
    "defi",
    "quant",
    "algo",
    "finance",
    "xtrades",
    "yuchi",
    "hummingbot",
    "openalgo",
    "ai4finance",
    "binance",
    "bybit",
    "signal",
    "profit",
    "copy",
    "twitter",
    "twit",
)

FORWARD_PATTERNS = (
    re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/", re.I),
    re.compile(r"https?://(?:www\.)?t\.me/", re.I),
    re.compile(r"https?://(?:www\.)?reddit\.com/", re.I),
    re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", re.I),
    re.compile(r"https?://(?:www\.)?tradingview\.com/", re.I),
    re.compile(r"https?://(?:www\.)?discord\.com/channels/", re.I),
)


def is_crypto(ch: dict) -> bool:
    blob = f"{ch.get('guild_name', '')} {ch.get('channel_name', '')}".lower()
    return any(k in blob for k in CRYPTO_KW)


def message_text(msg: dict) -> str:
    parts = [str(msg.get("content") or "")]
    for emb in msg.get("embeds") or []:
        if isinstance(emb, dict):
            parts.extend(
                str(emb.get(k) or "")
                for k in ("title", "description", "url", "author", "footer")
            )
            for f in emb.get("fields") or []:
                if isinstance(f, dict):
                    parts.append(str(f.get("name") or ""))
                    parts.append(str(f.get("value") or ""))
    return " ".join(parts)


def forward_score(msg: dict) -> bool:
    text = message_text(msg)
    if any(p.search(text) for p in FORWARD_PATTERNS):
        return True
    if msg.get("embeds"):
        return True
    if msg.get("attachments"):
        return True
    return False


def main() -> None:
    proc = subprocess.run(
        ["discord", "stats", "--json"], capture_output=True, text=True, check=True
    )
    stats = json.loads(proc.stdout)
    channels = stats.get("data", {}).get("channels", [])

    crypto = [c for c in channels if is_crypto(c)]
    crypto.sort(key=lambda c: int(c.get("msg_count") or 0), reverse=True)

    print("=== Crypto-related channels (cached msg_count >= 3) ===\n")
    print(f"{'msgs':>4}  {'channel_id':<20}  guild / channel  last_msg")
    print("-" * 90)
    for ch in crypto:
        mc = int(ch.get("msg_count") or 0)
        if mc < 3:
            continue
        gid = ch.get("guild_name", "?")
        cname = ch.get("channel_name", "?")
        last = str(ch.get("last_msg", ""))[:10]
        print(f"{mc:>4}  {ch['channel_id']:<20}  {gid}/{cname}  {last}")

    # Sample recent messages for forward ratio on top channels
    proc2 = subprocess.run(
        ["discord", "recent", "--json", "-n", "500"], capture_output=True, text=True
    )
    if proc2.returncode != 0:
        print("\n(recent sample skipped:", proc2.stderr.strip() or proc2.stdout[:200], ")")
        return

    recent = json.loads(proc2.stdout)
    if isinstance(recent, dict):
        recent = recent.get("data") or recent.get("messages") or []

    by_channel: dict[str, list] = {}
    for m in recent:
        if not isinstance(m, dict):
            continue
        cid = str(m.get("channel_id") or "")
        by_channel.setdefault(cid, []).append(m)

    ch_map = {str(c["channel_id"]): c for c in crypto}

    print("\n=== Forward-heavy estimate (sample from recent pool, per channel) ===\n")
    print(f"{'fwd%':>5}  {'n':>3}  {'channel_id':<20}  guild / channel")
    print("-" * 90)

    rows = []
    for cid, msgs in by_channel.items():
        ch = ch_map.get(cid)
        if not ch:
            continue
        n = len(msgs)
        if n < 2:
            continue
        fwd = sum(1 for m in msgs if forward_score(m))
        pct = 100.0 * fwd / n
        rows.append((pct, n, cid, ch))

    rows.sort(key=lambda r: (-r[0], -r[1], -int(r[3].get("msg_count") or 0)))
    for pct, n, cid, ch in rows[:25]:
        gid = ch.get("guild_name", "?")
        cname = ch.get("channel_name", "?")
        flag = " ***" if pct >= 70 and n >= 5 else ""
        print(f"{pct:>4.0f}%  {n:>3}  {cid:<20}  {gid}/{cname}{flag}")

    print("\n*** = sample looks mostly forwards/embeds/links (>=70% in recent pool)")


if __name__ == "__main__":
    main()
