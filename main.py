"""
DST 饥荒服务器管家插件 v3.1.0

通过 QQ 指令管理宿主机上的 Don't Starve Together 专用服务器。
依赖：容器 --pid=host（共享 PID 命名空间）、文件挂载。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from . import mod_manager, process, status


@register("dst_manager", "Hanako", "QQ 指令管理饥荒联机服务器", "3.1.0")
class DSTManagerPlugin(Star):
    """饥荒联机版（DST）服务器管理插件。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = config
        self._cluster: str = config.get("cluster", "DSTWhalesCluster")
        self._dst_base: str = config.get("dst_base", "/dst-server")
        self._klei_root: str = config.get("klei_root", "/root/.klei")
        self._startup_wait: int = int(config.get("startup_wait_seconds", 90))
        self._term_wait: int = int(config.get("graceful_term_wait", 15))
        self._steamcmd_timeout: int = int(config.get("mod_steamcmd_timeout", 180))

    # ── 状态 ──────────────────────────────────────────────

    @filter.command("饥荒状态")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看 DST 服务器运行状态和在线玩家。"""
        info = await asyncio.get_event_loop().run_in_executor(
            None, status.status_text, self._klei_root, self._cluster,
        )
        yield event.plain_result(f"🎮 饥荒服务器状态\n{info}")

    # ── 帮助 ──────────────────────────────────────────────

    @filter.command("饥荒帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示所有可用指令。"""
        yield event.plain_result(
            "🎮 饥荒服务器指令\n\n"
            "/饥荒状态      查看服务器状态\n"
            "/启动饥荒      启动服务器\n"
            "/冻结饥荒      暂停进程（秒级恢复）\n"
            "/解冻饥荒      恢复冻结的进程\n"
            "/恢复饥荒      同解冻\n"
            "/停服饥荒      完全关闭服务器\n"
            "/重启饥荒      重启服务器\n"
            "/连接教程      查看如何连接\n"
            "/饥荒模组      查看/管理模组\n"
            "/装模组 <ID>   安装模组（重启生效）\n"
            "/卸模组 <ID>   卸载模组（重启生效）\n"
            "/饥荒帮助      本帮助"
        )

    # ── 启动 ──────────────────────────────────────────────

    @filter.command("启动饥荒")
    async def cmd_start(self, event: AstrMessageEvent):
        """启动 DST 服务器（启动脚本后等待初始化）。"""
        m, c = await asyncio.get_event_loop().run_in_executor(
            None, process.get_pids, self._cluster,
        )
        if m or c:
            yield event.plain_result("⚠️ 服务器已经启动。如需重启，请先「停服饥荒」。")
            return

        yield event.plain_result(f"⏳ 正在启动 DST 服务器，预计等待 {self._startup_wait} 秒...")
        process.run_bg(f"bash {self._dst_base}/start.sh")
        await asyncio.sleep(self._startup_wait)

        info = await asyncio.get_event_loop().run_in_executor(
            None, status.status_text, self._klei_root, self._cluster,
        )
        yield event.plain_result(f"🎮 启动完成\n{info}")

    # ── 冻结/解冻 ─────────────────────────────────────────

    @filter.command("冻结饥荒")
    async def cmd_freeze(self, event: AstrMessageEvent):
        """冻结 DST 进程（SIGSTOP，秒级暂停）。"""
        m, c = await asyncio.get_event_loop().run_in_executor(
            None, process.get_pids, self._cluster,
        )
        if not m and not c:
            yield event.plain_result("❌ 服务器未运行，无需冻结。")
            return

        await asyncio.get_event_loop().run_in_executor(
            None, process.freeze_pids, [m, c],
        )
        info = await asyncio.get_event_loop().run_in_executor(
            None, status.status_text, self._klei_root, self._cluster,
        )
        yield event.plain_result(f"🧊 已冻结\n{info}")

    @filter.command("解冻饥荒")
    async def cmd_thaw(self, event: AstrMessageEvent):
        """解冻已冻结的 DST 进程（SIGCONT）。"""
        m, c = await asyncio.get_event_loop().run_in_executor(
            None, process.get_pids, self._cluster,
        )
        if not m and not c:
            yield event.plain_result("❌ 服务器未运行，请使用「启动饥荒」。")
            return

        states = []
        for p in filter(None, [m, c]):
            s = await asyncio.get_event_loop().run_in_executor(
                None, process.proc_state, p,
            )
            states.append(s)
        if all("T" not in s for s in states):
            yield event.plain_result("✅ 服务器已在运行中，无需解冻。")
            return

        await asyncio.get_event_loop().run_in_executor(
            None, process.thaw_pids, [m, c],
        )
        info = await asyncio.get_event_loop().run_in_executor(
            None, status.status_text, self._klei_root, self._cluster,
        )
        yield event.plain_result(f"▶️ 已解冻\n{info}")

    @filter.command("恢复饥荒")
    async def cmd_thaw_alias(self, event: AstrMessageEvent):
        """"恢复饥荒" 是 "解冻饥荒" 的别名。"""
        # Delegate to the thaw handler.
        async for result in self.cmd_thaw(event):
            yield result

    # ── 停服/重启 ─────────────────────────────────────────

    @filter.command("停服饥荒")
    async def cmd_stop(self, event: AstrMessageEvent):
        """完全关闭 DST 服务器（SIGTERM → 等待 → SIGKILL 兜底）。"""
        yield event.plain_result(f"⏹ 正在优雅关闭（SIGTERM → 等待 {self._term_wait} 秒 → 兜底 SIGKILL）...")

        stopped = await asyncio.get_event_loop().run_in_executor(
            None, process.graceful_stop, self._cluster, self._term_wait,
        )
        if not stopped:
            yield event.plain_result("❌ 服务器已经停服了。")
            return

        info = await asyncio.get_event_loop().run_in_executor(
            None, status.status_text, self._klei_root, self._cluster,
        )
        yield event.plain_result(f"⏹ 已停服\n{info}")

    @filter.command("重启饥荒")
    async def cmd_restart(self, event: AstrMessageEvent):
        """重启 DST 服务器。"""
        yield event.plain_result("⏳ 正在重启 DST 服务器...")

        stopped = await asyncio.get_event_loop().run_in_executor(
            None, process.graceful_stop, self._cluster, self._term_wait,
        )
        if not stopped:
            yield event.plain_result("❌ 未检测到运行中的 DST 进程")
            return

        process.run_bg(f"bash {self._dst_base}/start.sh")
        yield event.plain_result(f"⏳ 已发送重启指令，预计 {self._startup_wait} 秒完成...")
        await asyncio.sleep(self._startup_wait)

        info = await asyncio.get_event_loop().run_in_executor(
            None, status.status_text, self._klei_root, self._cluster,
        )
        yield event.plain_result(f"🔄 重启完成\n{info}")

    # ── 连接教程 ──────────────────────────────────────────

    @filter.command("连接教程")
    async def cmd_connect(self, event: AstrMessageEvent):
        """显示如何通过控制台直连服务器。"""
        yield event.plain_result(
            "🎮 如何连接饥荒服务器\n\n"
            "控制台直连：\n"
            "  1. 打开 DST，进入主菜单\n"
            "  2. 按 ~ 键打开控制台\n"
            "  3. 输入：\n"
            '    c_connect("你的服务器IP", 10999)\n'
            "  4. 回车即可进入\n\n"
            "💡 如果连不上，先确认服务器在线 /饥荒状态"
        )

    # ── 模组管理 ──────────────────────────────────────────

    @filter.command("饥荒模组")
    async def cmd_mods(self, event: AstrMessageEvent):
        """列出所有已配置的模组。"""
        installed = await asyncio.get_event_loop().run_in_executor(
            None, mod_manager.list_mods, self._dst_base, self._klei_root, self._cluster,
        )
        yield event.plain_result(
            "📦 模组管理\n\n"
            f"{installed}\n\n"
            "常用 ID：\n"
            "全局定位 378160973 | 小地图 374550642\n"
            "排队论 351325790 | 伤害显示 371247126\n"
            "木牌传送 666982465 | 复活祭坛 436769882\n"
            "不掉落 661253977 | 智能冰箱 359653495\n"
            "血量 466170003 | 五格装备 375850599\n\n"
            "装模组：/装模组 <ID>\n"
            "卸模组：/卸模组 <ID>\n"
            "生效需重启服务器"
        )

    @filter.command("装模组")
    async def cmd_add_mod(self, event: AstrMessageEvent):
        """安装模组（重启后生效）。"""
        parts = event.message_str.strip().split()
        wid = parts[-1] if len(parts) >= 2 else ""
        if not wid.isdigit():
            yield event.plain_result("格式：/装模组 <Workshop ID>\n例如：/装模组 378160973")
            return

        result = await asyncio.get_event_loop().run_in_executor(
            None, mod_manager.add_mod, wid, self._dst_base, self._klei_root, self._cluster, self._steamcmd_timeout,
        )
        if "warning" in result:
            yield event.plain_result(f"⚠️ 模组 {wid} 配置已写入但下载可能失败：{result}\n重启服务器看效果（/停服饥荒 → /启动饥荒）")
        else:
            yield event.plain_result(f"✅ 已添加模组 {wid}，重启服务器后生效（/停服饥荒 → /启动饥荒）")

    @filter.command("卸模组")
    async def cmd_remove_mod(self, event: AstrMessageEvent):
        """卸载模组（重启后生效）。"""
        parts = event.message_str.strip().split()
        wid = parts[-1] if len(parts) >= 2 else ""
        if not wid.isdigit():
            yield event.plain_result("格式：/卸模组 <Workshop ID>\n例如：/卸模组 378160973")
            return

        result = await asyncio.get_event_loop().run_in_executor(
            None, mod_manager.remove_mod, wid, self._dst_base, self._klei_root, self._cluster,
        )
        yield event.plain_result(f"✅ 已移除模组 {wid}，重启服务器后生效（/停服饥荒 → /启动饥荒）")

    async def terminate(self) -> None:
        """插件卸载/禁用时的清理。"""
        logger.info("[dst] 插件已卸载")
