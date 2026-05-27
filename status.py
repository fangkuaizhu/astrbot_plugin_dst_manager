"""
DST 服务器状态信息提取模块。

从 server_log.txt 解析季节、天数、玩家状态等游戏内信息。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from astrbot.api import logger

from . import process


SEASON_CN: dict[str, str] = {
    "autumn": "秋",
    "winter": "冬",
    "spring": "春",
    "summer": "夏",
}


def _human(stat: int) -> str:
    if stat <= 10:
        return "垂死挣扎"
    if stat <= 25:
        return "奄奄一息"
    if stat <= 50:
        return "遍体鳞伤"
    if stat <= 75:
        return "受了点伤"
    return "😎 状态良好"


def _sanity_desc(stat: int) -> str:
    if stat <= 10:
        return "🤯 精神崩溃"
    if stat <= 25:
        return "😵 神志不清"
    if stat <= 50:
        return "😰 心神不宁"
    if stat <= 75:
        return "😟 有点焦虑"
    return "😊 头脑清醒"


def _hunger_desc(stat: int) -> str:
    if stat <= 10:
        return "💀 即将饿死"
    if stat <= 25:
        return "🦴 饥肠辘辘"
    if stat <= 50:
        return "🍞 有些饿了"
    if stat <= 75:
        return "🍗 吃个半饱"
    return "🍖 饱餐一顿"


def _read_shard_log(klei_root: str, cluster: str, shard: str) -> list[str]:
    """读取某个分片的 server_log 最后 200 行。"""
    path = (
        Path(klei_root)
        / "DoNotStarveTogether"
        / cluster
        / shard
        / "server_log.txt"
    )
    if not path.exists():
        return []
    try:
        text = path.read_text(errors="replace")
        return text.splitlines()[-200:]
    except Exception:
        return []


def _get_disconnected_names(
    klei_root: str, cluster: str, shard: str
) -> set[str]:
    """从 server_log 尾部逆向扫描，检测已下线的玩家名。"""
    log_lines = _read_shard_log(klei_root, cluster, shard)
    if not log_lines:
        return set()

    ku_to_name: dict[str, str] = {}
    ku_last_status: dict[str, str] = {}
    re_ku_name = re.compile(r"\[\((KU_\w+)\)\s+(\S+)\]")
    re_disconnect = re.compile(r"\((KU_\w+)\)\s+disconnected")

    for line in reversed(log_lines):
        m = re_disconnect.search(line)
        if m:
            ku = m.group(1)
            if ku not in ku_last_status:
                ku_last_status[ku] = "offline"
                continue
        m = re_ku_name.search(line)
        if m:
            ku = m.group(1)
            name = m.group(2)
            ku_to_name[ku] = name
            if ku not in ku_last_status:
                ku_last_status[ku] = "online"

    return {
        ku_to_name.get(ku)
        for ku, status in ku_last_status.items()
        if status == "offline" and ku in ku_to_name
    }


def game_info(klei_root: str, cluster: str) -> str:
    """从 server_log 获取 StatsLog 并合并显示（过滤已下线玩家）。"""
    parts: list[str] = []
    day_val = 0
    season_val: Optional[str] = None
    all_players: dict[str, dict] = {}

    offline_master = _get_disconnected_names(klei_root, cluster, "Master")
    offline_caves = _get_disconnected_names(klei_root, cluster, "Caves")

    for shard, world_name in [("Master", "🌍 地面"), ("Caves", "🕳️ 洞穴")]:
        log_lines = _read_shard_log(klei_root, cluster, shard)
        if not log_lines:
            continue
        stats_line = ""
        for line in log_lines:
            if "[StatsLog]" in line and "{" in line:
                stats_line = line
        if stats_line:
            try:
                m = re.search(r"\[StatsLog\]\s*(\{.*\})", stats_line)
                if m:
                    data = json.loads(m.group(1))
                    if data.get("d"):
                        day_val = max(day_val, data["d"])
                    if data.get("se") and not season_val:
                        season_val = data["se"]
                    for p in data.get("players", []):
                        name = p.get("n", "?")
                        offline = offline_master if shard == "Master" else offline_caves
                        if name in offline:
                            continue
                        all_players[name] = {
                            "world": world_name,
                            "h": p.get("h", 0),
                            "u": p.get("u", 0),
                            "s": p.get("s", 0),
                        }
            except Exception:
                continue

    if day_val:
        parts.append(f"📅 第 {day_val} 天")
    if season_val:
        parts.append(f"🌤 {SEASON_CN.get(season_val, season_val)}季")

    if all_players:
        for name, p in sorted(all_players.items()):
            parts.append(f"  🎮 {name}（{p['world']}）")
            parts.append(
                f"    ❤️{p['h']}% {_human(p['h'])}  "
                f"🍖{p['u']}% {_hunger_desc(p['u'])}  "
                f"🧠{p['s']}% {_sanity_desc(p['s'])}"
            )
    else:
        parts.append("👥 当前无人在线")

    return "\n".join(parts)


def status_text(
    klei_root: str,
    cluster: str,
) -> str:
    """生成完整服务器状态文本。"""
    master_pid, caves_pid = process.get_pids(cluster)
    if not master_pid and not caves_pid:
        return "❌ 已停服"

    lines: list[str] = []
    for name, pid in [("地面", master_pid), ("洞穴", caves_pid)]:
        if pid:
            s = process.proc_state(pid)
            if "T (stopped)" in s or "T (tracing stop)" in s:
                lines.append(f"  🧊 {name} (PID {pid}) — 已冻结")
            elif "S (sleeping)" in s or "R (running)" in s:
                lines.append(f"  ✅ {name} (PID {pid}) — 运行中")
            else:
                lines.append(f"  ⚠️ {name} (PID {pid}) — {s}")
        else:
            lines.append(f"  ❌ {name} — 未运行")

    info = game_info(klei_root, cluster)
    if info:
        lines.append("")
        lines.append(info)

    return "\n".join(lines)
