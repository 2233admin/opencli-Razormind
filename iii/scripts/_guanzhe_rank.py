"""Rank probed 观察者聚合 channels from discord stats cache."""
from __future__ import annotations

import json
from pathlib import Path

STATS = Path(r"C:\Users\Administrator\agent-tools\guanzhe_stats.json")

CHANNELS = [
    ("1357021092023111770", "📖社区交流群", "群聊"),
    ("1357260741530353795", "内部群一群", "群聊"),
    ("1359880424465236163", "内部群二群", "群聊"),
    ("1369589295937224736", "内部群三群", "群聊"),
    ("1374288068583886879", "内部群四群", "群聊"),
    ("1391443355241545760", "内部群五群", "群聊"),
    ("1357993619025301715", "🎤公告群（不要静音）", "公告"),
    ("1362645394122866880", "💰盈亏分享", "社区"),
    ("1357955612075360431", "📈博主战绩统计", "统计"),
    ("1376437254137839616", "交流群精华汇总", "汇总"),
    ("1411607786265251910", "✖️推特通知", "转发"),
    ("1410565827975053363", "🎬视频通知", "转发"),
    ("1426935728444543108", "🔔订阅频道", "转发"),
    ("1368893581594460170", "【必看】跟单建议", "转发"),
    ("1340919349023739986", "🌸舒琴行情分析", "分析"),
    ("1340920781978472458", "📊｜交易策略汇总（入场）", "策略汇总"),
    ("1340920804497690654", "🚨｜策略警报汇总（止盈止损）", "策略汇总"),
    ("1356701964141985952", "💨｜woods分析", "分析"),
    ("1340920849737453639", "👑｜johnny分析", "分析"),
    ("1379781458620710982", "🚨｜astekz策略", "策略"),
]


def main() -> None:
    raw = STATS.read_text(encoding="utf-8-sig")
    stats = json.loads(raw)
    by_id = {str(x["channel_id"]): x for x in stats["data"]["channels"]}

    rows = []
    for cid, name, kind in CHANNELS:
        ch = by_id.get(cid, {})
        mc = int(ch.get("msg_count") or 0)
        last = str(ch.get("last_msg") or "")[:10]
        rows.append((mc, last, kind, cid, name))

    rows.sort(reverse=True)
    print("msg_count  last_msg    kind      channel_id           name")
    print("-" * 90)
    for mc, last, kind, cid, name in rows:
        print(f"{mc:>9}  {last:10}  {kind:8}  {cid}  {name}")


if __name__ == "__main__":
    main()
