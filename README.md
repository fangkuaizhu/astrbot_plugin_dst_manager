# DST 饥荒服务器管家

通过 QQ 指令管理宿主机上的 Don't Starve Together（饥荒联机版）专用服务器。

## 依赖

- **AstrBot** >= v4.16
- **容器配置**：`--pid=host`（共享 PID 命名空间）+ 文件挂载到 `/dst-server` 和 `/root/.klei`
- **steamcmd**（模组管理可选）：Docker 镜像 `cm2network/steamcmd`

## 指令

| 指令 | 说明 |
|---|---|
| `/饥荒状态` | 查看服务器状态、在线玩家 |
| `/饥荒帮助` | 显示帮助 |
| `/启动饥荒` | 启动服务器（~90 秒） |
| `/冻结饥荒` | 暂停进程（SIGSTOP，秒级恢复） |
| `/解冻饥荒` | 恢复冻结的进程 |
| `/恢复饥荒` | 同解冻 |
| `/停服饥荒` | 完全关闭（SIGTERM → SIGKILL） |
| `/重启饥荒` | 重启服务器 |
| `/连接教程` | 显示客户端直连方式 |
| `/饥荒模组` | 列出已安装/已配置模组 |
| `/装模组 <ID>` | 安装模组（重启生效） |
| `/卸模组 <ID>` | 卸载模组（重启生效） |

## 配置

在 AstrBot WebUI → 插件管理 → DST 饥荒服务器管家 中可配置：

- `dst_base`：DST 安装目录（默认 `/dst-server`）
- `klei_root`：Klei 存档根目录（默认 `/root/.klei`）
- `cluster`：集群名称（默认 `DSTWhalesCluster`）
- `startup_wait_seconds`：启动等待时间（默认 90）
- `graceful_term_wait`：优雅停止等待（默认 15）
- `mod_steamcmd_timeout`：模组下载超时（默认 180）

## 项目结构

```
astrbot_plugin_dst_manager/
├── __init__.py       # 空文件，包标识
├── main.py           # 插件入口，指令路由
├── process.py        # 进程管理（PID 扫描、信号控制）
├── status.py         # 服务器状态、游戏信息解析
├── mod_manager.py    # 模组安装/卸载/查询
├── metadata.yaml     # 插件元信息
├── _conf_schema.json # 配置定义
├── requirements.txt  # 依赖声明
└── README.md         # 本文件
```
