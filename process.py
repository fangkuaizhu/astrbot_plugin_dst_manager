"""
DST 进程管理模块。

对宿主 DST 进程的扫描、信号控制、本地命令执行。
依赖容器共享 PID 命名空间（--pid=host）和文件挂载。
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from astrbot.api import logger


# ─── 本地命令执行 ───────────────────────────────────────────


def run_sync(cmd: str, timeout: int = 30) -> str:
    """同步执行 shell 命令，返回 stdout+stderr 合并输出。"""
    try:
        r = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True,
            timeout=timeout,
        )
        return (r.stdout + r.stderr).strip() or "(empty)"
    except subprocess.TimeoutExpired:
        logger.warning(f"[dst] 命令超时: {cmd[:80]}")
        return f"超时: {cmd[:80]}"
    except Exception as e:
        logger.warning(f"[dst] 执行失败: {e}")
        return f"执行失败: {e}"


def run_bg(cmd: str) -> None:
    """后台启动进程，不等待、不捕获输出。"""
    subprocess.Popen(
        ["bash", "-c", cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def run_async(cmd: str, timeout: int = 30) -> str:
    """异步执行本地命令（通过线程池避免阻塞事件循环）。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_sync, cmd, timeout)


# ─── PID 扫描 ─────────────────────────────────────────────


def find_dst_pids(cluster: str) -> list[int]:
    """扫描 /proc 获取所有匹配的 DST 进程 PID。"""
    pids: list[int] = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_text(errors="replace")
                if "dontstarve_dedicated_server_nullrenderer" in cmdline and cluster in cmdline:
                    pids.append(int(entry.name))
            except (IOError, PermissionError, ValueError):
                continue
    except Exception as e:
        logger.warning(f"[dst] 扫描 PID 失败: {e}")
    return pids


def get_pids(cluster: str) -> tuple[Optional[int], Optional[int]]:
    """返回 (master_pid, caves_pid)，通过 cmdline 中 -shard 区分。"""
    pids = find_dst_pids(cluster)
    if not pids:
        return None, None
    master_pid: Optional[int] = None
    caves_pid: Optional[int] = None
    for pid in pids:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_text(errors="replace")
            if "Master" in cmdline:
                master_pid = pid
            elif "Caves" in cmdline:
                caves_pid = pid
        except Exception:
            continue
    return master_pid, caves_pid


def proc_state(pid: int) -> str:
    """获取进程状态字符串，如 'State: S (sleeping)'。"""
    try:
        status = Path(f"/proc/{pid}/status").read_text(errors="replace")
        for line in status.splitlines():
            if line.startswith("State:"):
                return line.strip()
    except Exception:
        pass
    return "N/A"


# ─── 信号控制 ─────────────────────────────────────────────


def kill_pids(pids: list[Optional[int]], sig: int, grace: float = 0) -> None:
    """向进程列表发送信号，可选发送后休眠等待。"""
    for pid in pids:
        if pid is None:
            continue
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning(f"[dst] kill({pid},{sig}) 失败: {e}")
    if grace > 0:
        time.sleep(grace)


def freeze_pids(pids: list[Optional[int]]) -> None:
    """冻结进程（SIGSTOP）。"""
    kill_pids(pids, signal.SIGSTOP)


def thaw_pids(pids: list[Optional[int]]) -> None:
    """解冻进程（SIGCONT）。"""
    kill_pids(pids, signal.SIGCONT)


def graceful_stop(
    cluster: str,
    term_wait: int = 15,
    kill_wait: int = 2,
) -> bool:
    """优雅停止所有 DST 进程：SIGTERM → 等待 → SIGKILL 兜底。

    返回 True 表示有进程被停止，False 表示本来就无进程。
    """
    m, c = get_pids(cluster)
    if not m and not c:
        return False
    kill_pids([m, c], signal.SIGTERM, grace=term_wait)
    still_alive = find_dst_pids(cluster)
    if still_alive:
        kill_pids(still_alive, signal.SIGKILL, grace=kill_wait)
    return True
