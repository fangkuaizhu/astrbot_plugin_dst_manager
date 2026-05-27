"""
DST 模组管理模块。

通过本地文件读写和 steamcmd Docker 容器实现模组安装/卸载/查询。
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from astrbot.api import logger


# 常见模组 ID → 中文名映射（离线兜底）
KNOWN_MOD_NAMES: dict[str, str] = {
    "378160973": "Global Positions",
    "3572909709": "Minimap HUD",
    "661253977": "Don't Drop Everything",
    "1730366029": "Show Me (中文)",
    "2784048339": "Storeroom (New)",
    "2287303119": "Insight (Show Me +)",
}


def _read_mod_setup(dst_base: str) -> str:
    """读取 dedicated_server_mods_setup.lua。"""
    path = Path(dst_base) / "dedicated_server_mods_setup.lua"
    if path.exists():
        return path.read_text(errors="replace")
    return ""


def _read_mod_overrides(klei_root: str, cluster: str, shard: str) -> str:
    """读取某个分片的 modoverrides.lua。"""
    path = (
        Path(klei_root)
        / "DoNotStarveTogether"
        / cluster
        / shard
        / "modoverrides.lua"
    )
    if path.exists():
        return path.read_text(errors="replace")
    return "return {}"


def _write_mod_overrides(klei_root: str, cluster: str, shard: str, content: str) -> None:
    """写入某个分片的 modoverrides.lua。"""
    path = (
        Path(klei_root)
        / "DoNotStarveTogether"
        / cluster
        / shard
        / "modoverrides.lua"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_line(filepath: str, line: str) -> None:
    """安全追加一行到文件（去重，精确行匹配）。"""
    p = Path(filepath)
    if p.exists():
        lines = p.read_text(errors="utf-8").splitlines()
        if line not in lines:
            p.write_text("\n".join(lines) + "\n" + line + "\n", encoding="utf-8")
    else:
        p.write_text(line + "\n", encoding="utf-8")


def _remove_line(filepath: str, pattern: str) -> None:
    """从文件中删除包含 pattern 的行。"""
    p = Path(filepath)
    if not p.exists():
        return
    text = p.read_text(errors="utf-8")
    new_lines = [l for l in text.splitlines() if pattern not in l]
    p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _steam_query_names(ids: list[str]) -> dict[str, str]:
    """通过 Steam API 批量查询模组名称。"""
    names: dict[str, str] = {}
    try:
        for chunk in [ids[i:i + 10] for i in range(0, len(ids), 10)]:
            params = {"itemcount": str(len(chunk))}
            for j, wid in enumerate(chunk):
                params[f"publishedfileids[{j}]"] = wid
            data = urllib.parse.urlencode(params).encode()
            req = urllib.request.Request(
                "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                data=data,
            )
            r = urllib.request.urlopen(req, timeout=10)
            resp = json.loads(r.read())
            for item in resp.get("response", {}).get("publishedfiledetails", []):
                title = item.get("title", "").strip()
                if title:
                    names[str(item.get("publishedfileid", ""))] = title
    except Exception as e:
        logger.warning(f"[dst] Steam API 查询失败: {e}")
    return names


def list_mods(dst_base: str, klei_root: str, cluster: str) -> str:
    """列出所有已配置的模组，标记启用状态。"""
    setup_text = _read_mod_setup(dst_base)
    ov_text_master = _read_mod_overrides(klei_root, cluster, "Master")

    all_ids: set[str] = set()
    enabled_ids: set[str] = set()

    ids = re.findall(r'ServerModSetup\("(\d+)"\)', setup_text)
    all_ids.update(ids)

    ids = re.findall(r'"workshop-(\d+)"', ov_text_master)
    enabled_ids.update(ids)
    all_ids.update(ids)

    if not all_ids:
        return "当前未配置任何模组"

    # Steam API 查询名称
    id_list = list(all_ids)
    names = _steam_query_names(id_list)
    # 补充本地已知名称
    for mid, lname in KNOWN_MOD_NAMES.items():
        if mid not in names:
            names[mid] = lname

    lines: list[str] = []
    for mid in sorted(all_ids):
        name = names.get(mid, f"Workshop {mid}")
        flag = "✅" if mid in enabled_ids else "📥"
        lines.append(f"  {flag} {name}")

    return "\n".join(lines)


def add_mod(
    wid: str,
    dst_base: str,
    klei_root: str,
    cluster: str,
    steamcmd_timeout: int = 180,
) -> str:
    """安装模组：写入配置 → steamcmd 下载。返回 'ok' 或警告信息。"""
    # 1. 写入 setup 文件
    setup_line = f'ServerModSetup("{wid}")'
    _append_line(f"{dst_base}/dedicated_server_mods_setup.lua", setup_line)

    # 2. 写入 modoverrides（Master + Caves）
    ov_entry = f'  ["workshop-{wid}"] = {{ enabled = true }},'
    for shard in ("Master", "Caves"):
        content = _read_mod_overrides(klei_root, cluster, shard)
        ov_key = f'"workshop-{wid}"'
        if ov_key in content:
            continue
        content = content.rstrip()
        last_brace = content.rfind("}")
        if last_brace >= 0:
            content = content[:last_brace].rstrip()
            content += "\n" + ov_entry + "\n}\n"
        else:
            content = "return {\n" + ov_entry + "\n}\n"
        _write_mod_overrides(klei_root, cluster, shard, content)

    # 3. steamcmd 下载模组
    try:
        r = subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{dst_base}:/dst",
                "cm2network/steamcmd",
                "+force_install_dir", "/dst",
                "+login", "anonymous",
                "+workshop_download_item", "322330", wid,
                "+quit",
            ],
            capture_output=True, text=True,
            timeout=steamcmd_timeout,
        )
        if r.returncode != 0:
            logger.warning(f"[dst] 模组下载失败 (exit {r.returncode}): {r.stderr[-200:]}")
            return f"warning: 配置已写入但 steamcmd 下载失败 (exit {r.returncode})"
    except subprocess.TimeoutExpired:
        logger.warning(f"[dst] 模组下载超时 ({steamcmd_timeout}s)")
        return "warning: 配置已写入但 steamcmd 下载超时"
    except FileNotFoundError:
        logger.warning("[dst] docker 命令不存在，无法下载模组")
        return "warning: 配置已写入但无法调用 docker 下载模组"

    return "ok"


def remove_mod(wid: str, dst_base: str, klei_root: str, cluster: str) -> str:
    """卸载模组：从配置文件中移除。"""
    # 1. 从 setup 移除
    _remove_line(
        f"{dst_base}/dedicated_server_mods_setup.lua",
        f'ServerModSetup("{wid}")',
    )

    # 2. 从 modoverrides 移除（Master + Caves）
    ov_key = f'"workshop-{wid}"'
    for shard in ("Master", "Caves"):
        path = (
            Path(klei_root)
            / "DoNotStarveTogether"
            / cluster
            / shard
            / "modoverrides.lua"
        )
        _remove_line(str(path), ov_key)
        if path.exists():
            remain = path.read_text(errors="utf-8").strip()
            if not remain:
                path.write_text("return {}\n", encoding="utf-8")
    return "ok"
