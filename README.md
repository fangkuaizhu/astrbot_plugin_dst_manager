# DST 饥荒服务器管家

通过 QQ 指令管理 Don't Starve Together（饥荒联机版）专用服务器。

## 架构原理

```
┌─────────────────────────────────────────────────────┐
│                    宿主机 (VPS)                       │
│                                                      │
│  ┌──────────────┐    ┌──────────────────────────┐   │
│  │  AstrBot 容器 │    │     DST 服务器 (原生进程)  │   │
│  │  (Docker)     │    │                          │   │
│  │  ┌─────────┐  │    │  ┌────────────────────┐  │   │
│  │  │ 本插件  │──┼────┼─▶│ 扫描 /proc/{pid}    │  │   │
│  │  │ main.py │  │    │  │ 发 SIGSTOP/SIGCONT  │  │   │
│  │  └─────────┘  │    │  │ 读 server_log.txt   │  │   │
│  │       │       │    │  └────────────────────┘  │   │
│  │       ▼       │    │                          │   │
│  │  ┌─────────┐  │    │  Master (地面)  PID: x   │   │
│  │  │ 子进程跑  │  │    │  Caves  (洞穴)  PID: y   │   │
│  │  │ bash 命令 │  │    │                          │   │
│  │  └─────────┘  │    └──────────────────────────┘   │
│  └──────────────┘                                     │
│                                                      │
│  ┌──────────────┐    ┌──────────────────────────┐   │
│  │ NapCat 容器   │    │  Klei 存档目录            │   │
│  │ (QQ 客户端)   │    │  ~/.klei/.../server_log  │   │
│  └──────────────┘    └──────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### 工作原理

插件通过 AstrBot 容器的 `--pid=host` 参数与宿主机共享 PID 命名空间，从而能直接管理宿主机上的 DST 进程：

| 操作 | 实现方式 |
|---|---|
| **启动/重启** | 在容器内通过 `subprocess` 执行宿主机上的 `start.sh`（需文件挂载） |
| **冻结/解冻** | 向 DST 进程发送 `SIGSTOP` / `SIGCONT` 信号 |
| **停服** | 先发 `SIGTERM` 等待 15 秒，未退出则 `SIGKILL` 兜底 |
| **状态读取** | 直接读取挂载的 `server_log.txt` 解析 StatsLog JSON |
| **模组管理** | 修改 `modoverrides.lua` + 调用 steamcmd Docker 下载 |

### 三个关键依赖

1. **`--pid=host`** — AstrBot 容器必须共享宿主 PID 命名空间，才能 `kill()` DST 进程
2. **文件挂载** — AstrBot 容器需要挂载 `/root/.klei`（读日志）和 DST 安装目录（执行启动脚本、改模组配置）
3. **`/proc` 可读** — 插件通过扫描 `/proc/{pid}/cmdline` 查找 DST 进程

---

## 部署教程

### 1. DST 服务器搭建（宿主机操作）

#### 1.1 安装依赖

```bash
# 64-bit 依赖（Alibaba Cloud Linux 3 / CentOS / Ubuntu）
yum install -y screen libcurl-devel zlib-devel  # RHEL 系
# apt install -y screen libcurl4 zlib1g-dev      # Debian 系
```

#### 1.2 安装 steamcmd

```bash
mkdir -p /dst-server && cd /dst-server
curl -sqL "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" | tar zxvf -
```

#### 1.3 安装 DST 服务端

```bash
cd /dst-server
./steamcmd.sh +login anonymous +force_install_dir /dst-server +app_update 343050 validate +quit
```

#### 1.4 配置集群

创建存档目录：

```bash
mkdir -p ~/.klei/DoNotStarveTogether/DSTWhalesCluster/Master
mkdir -p ~/.klei/DoNotStarveTogether/DSTWhalesCluster/Caves
```

必须包含以下文件（示例可从本地 DST 客户端 `Documents\Klei\DoNotStarveTogether\` 拷贝）：

```
~/.klei/DoNotStarveTogether/DSTWhalesCluster/
├── cluster.ini          # 集群配置（名称、密码、模式）
├── cluster_token.txt    # Klei 服务器 Token（从 Klei 官网获取）
├── Master/
│   ├── server.ini       # 地面世界配置
│   └── modoverrides.lua # 模组配置
└── Caves/
    ├── server.ini       # 洞穴世界配置
    └── modoverrides.lua
```

**注意**：DST 使用 64-bit 二进制，路径为 `/dst-server/bin64/dontstarve_dedicated_server_nullrenderer_x64`。

#### 1.5 启动脚本（/dst-server/start.sh）

```bash
#!/bin/bash
INSTALL_DIR="/dst-server"
CLUSTER="DSTWhalesCluster"
CONFIG_DIR="$HOME/.klei/DoNotStarveTogether/$CLUSTER"
BIN="$INSTALL_DIR/bin64/dontstarve_dedicated_server_nullrenderer_x64"
export LD_LIBRARY_PATH="$INSTALL_DIR/bin64/lib64"

cd "$INSTALL_DIR/bin64" || exit 1

screen -dmS dst_master "$BIN" -console -cluster "$CLUSTER" -shard Master -monitor_parent_process 0
sleep 5
screen -dmS dst_caves  "$BIN" -console -cluster "$CLUSTER" -shard Caves  -monitor_parent_process 0
```

> `-monitor_parent_process 0` 很重要：设为 0 禁止 DST 在父进程（screen）退出时自杀。

#### 1.6 测试启动

```bash
chmod +x /dst-server/start.sh
bash /dst-server/start.sh
sleep 90
screen -ls              # 应看到 dst_master 和 dst_caves
```

---

### 2. AstrBot 容器配置

#### 2.1 docker-compose.yml

```yaml
version: "3.8"
services:
  astrbot:
    image: soulter/astrbot:latest
    container_name: astrbot
    ports:
      - "6185:6185"      # AstrBot WebUI
    volumes:
      - ./astrbot:/AstrBot/data              # 数据持久化
      - /dst-server:/dst-server              # 挂载 DST 安装目录（读启动脚本、写模组配置）
      - /root/.klei:/root/.klei              # 挂载 Klei 存档（读 server_log、改 modoverrides）
      - /var/run/docker.sock:/var/run/docker.sock:ro  # 可选：模组管理调 steamcmd 需要
    pid: "host"                               # ⚠️ 关键：共享 PID 命名空间
    restart: unless-stopped

  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    environment:
      - NAPCAT_UID=0
      - NAPCAT_GID=0
    volumes:
      - ./napcat:/app/.config/QQ
    restart: unless-stopped
```

**必须的三项配置：**

| 配置项 | 作用 |
|---|---|
| `pid: "host"` | 让容器能访问宿主机全部 `/proc/{pid}`，发送信号 |
| `- /dst-server:/dst-server` | 容器内执行 `/dst-server/start.sh`、读写模组配置 |
| `- /root/.klei:/root/.klei` | 容器内读取 `server_log.txt` 获取游戏状态 |

#### 2.2 验证挂载生效

```bash
# 进入容器确认
docker exec astrbot ls /root/.klei/DoNotStarveTogether/DSTWhalesCluster/Master/
docker exec astrbot ls /dst-server/bin64/
# 确认能看到 DST 进程
docker exec astrbot ps aux | grep dontstarve
```

#### 2.3 安装插件

在 AstrBot WebUI → 插件管理 → 安装插件 → 输入仓库地址：

```
https://github.com/fangkuaizhu/astrbot_plugin_dst_manager
```

或手动复制到 `data/plugins/` 目录后 `/plugin reload`。

#### 2.4 配置插件

WebUI → 插件管理 → DST 饥荒服务器管家 → 配置：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `dst_base` | `/dst-server` | DST 安装目录（容器内路径） |
| `klei_root` | `/root/.klei` | Klei 存档根目录 |
| `cluster` | `DSTWhalesCluster` | 集群名称 |
| `startup_wait_seconds` | `90` | 启动等待秒数 |
| `graceful_term_wait` | `15` | 优雅停止等待秒数 |
| `mod_steamcmd_timeout` | `180` | 模组下载超时秒数 |

如果不使用模组管理功能，`docker.sock` 挂载可以去掉。

---

## 指令

| 指令 | 说明 |
|---|---|
| `/饥荒状态` | 查看服务器状态、在线玩家、血量/饱食/精神 |
| `/饥荒帮助` | 显示帮助 |
| `/启动饥荒` | 启动服务器（约 90 秒） |
| `/冻结饥荒` | 暂停进程（SIGSTOP，秒级恢复） |
| `/解冻饥荒` | 恢复冻结的进程 |
| `/恢复饥荒` | 同解冻 |
| `/停服饥荒` | 完全关闭（SIGTERM → SIGKILL） |
| `/重启饥荒` | 重启服务器 |
| `/连接教程` | 显示客户端控制台直连方式 |
| `/饥荒模组` | 列出已安装/已配置模组 |
| `/装模组 <ID>` | 安装模组（重启生效） |
| `/卸模组 <ID>` | 卸载模组（重启生效） |

---

## 项目结构

```
astrbot_plugin_dst_manager/
├── __init__.py       # 空文件，包标识
├── main.py           # 插件入口，指令路由
├── process.py        # 进程管理（PID 扫描、信号控制、命令执行）
├── status.py         # 服务器状态、游戏信息解析
├── mod_manager.py    # 模组安装/卸载/查询
├── metadata.yaml     # 插件元信息
├── _conf_schema.json # WebUI 可配置项
├── requirements.txt  # 依赖声明
├── logo.png          # 插件图标
└── README.md         # 本文件
```

## 常见问题

**Q: 插件报 `ApiNotAvailable`**

NapCat 与 QQ 的连接断开了。检查 NapCat 日志：`docker logs napcat | grep error`。常见原因是容器内 QQ 渲染进程崩溃（GPU 环境缺失），这是 NapCat 在该环境下的已知问题。

**Q: `找不到 DST 进程`**

确认 `docker exec astrbot ps aux | grep dontstarve` 能看到进程。如果看不到，说明 `--pid=host` 未生效，或 DST 未在宿主机上运行。

**Q: `无法执行 start.sh`**

确认 `/dst-server` 已挂载到容器内并且路径与配置一致。检查 `docker exec astrbot ls /dst-server/start.sh`。

**Q: 模组下载失败**

检查宿主机能否执行 `docker run --rm cm2network/steamcmd ...`。部分 VPS 的 Docker 网络需要代理才能访问 Steam。确认 `docker.sock` 已挂载到 AstrBot 容器。
