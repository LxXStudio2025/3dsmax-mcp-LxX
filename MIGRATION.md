# 迁移清单

把本项目搬到新目录 / 新会话前，按顺序做。**Max 端不需要任何改动**，主要风险在
Python 虚拟环境和 MCP 配置里的绝对路径。

---

## 1. 搬之前

```bash
# 关掉 Max（bridge 跑在 Max 进程内，但不影响文件）
# 关掉 WorkBuddy 里 3dsmax2016 / 3dsmax2025 / dcc 三个 connector
```

**不需要备份数据库或配置**——所有状态都在项目目录里，除了下面的两个例外。

---

## 2. 搬什么，不搬什么

| 搬 | 说明 |
|---|---|
| `max-mcp/` 整个目录（**含 `.git`**） | 源码、文档、API 快照、版本历史 |
| `dcc-mcp/` 整个目录 | dcc-bridge 的 MCP 包装层 |

| **不要搬** | 原因 |
|---|---|
| `max-mcp/.venv/` | **Python venv 不可重定位**——`pyvenv.cfg` 和 `Scripts/*.exe` 里写死了旧路径，搬过去会报 `Fatal error in launcher`。到新位置重建。 |
| `tools/docs/maxscript-*/` | 抓取的 Autodesk 文档（16 MB），已在 `.gitignore` 里，版权原因不随仓库走。需要时用 `tools/crawl_maxscript_docs.py` 重建。 |

---

## 3. 搬之后

### 3.1 重建虚拟环境

```bat
cd <新目录>\max-mcp
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pip install -e <新目录>\..\仓库\DCC-Python-main\packages\dcc-bridge
```

第二条只有还要用 `dcc` 那个 MCP 时才需要。

### 3.2 改两个 MCP 配置路径

编辑 `~/.workbuddy/mcp.json`，把四处路径换成新目录：

```json
"3dsmax2016": {
  "command": "<新目录>\\max-mcp\\.venv\\Scripts\\python.exe",
  "args": ["-m", "max_mcp.server"],
  "env": { "MAXMCP_PORT": "47100" }
},
"3dsmax2025": { "env": { "MAXMCP_PORT": "47109" } },   // command 同上
"dcc": {
  "command": "<新目录>\\max-mcp\\.venv\\Scripts\\python.exe",
  "args": ["<新目录>\\dcc-mcp\\server.py"]
}
```

改完在 WorkBuddy 里**刷新并重新信任**，然后**新开会话**——工具列表在会话初始化时固定。

### 3.3 Max 端：什么都不用做

bridge 装在 `<Max 安装目录>\scripts\maxmcp\max_mcp_bridge.ms`，
launcher 在 `<Max 安装目录>\scripts\startup\start_maxmcp.ms`，
两者都指向 Max 目录内的路径，**与项目目录无关**。

只有一种情况需要重装：你希望用新目录里的新版脚本覆盖 Max 里那份，那就跑

```bat
python tools\install.py --install --lang en --user
```

---

## 4. 验证

```bat
.venv\Scripts\maxctl.exe --port 47100 status     :: 应返回 max_version 2016
.venv\Scripts\maxctl.exe --port 47109 status     :: 应返回 max_version 2025
```

连不上时按层排查：

| 现象 | 含义 | 看哪里 |
|---|---|---|
| `MaxNotConnected` | Max 没开，或 bridge 没加载 | `%TEMP%\maxmcp-launcher-<年份>.txt` 在不在 |
| 端口无 `SYN_SENT`/`LISTENING` | bridge 没在轮询 | `%TEMP%\maxmcp-boot-<年份>.log` 有没有 `boot finished` |
| `LISTENING` 但无 `ESTABLISHED` | MCP server 起了但 Max 没连上 | 端口是否对（47100 / 47109） |

---

## 5. 新会话开场

新 workspace 是新记忆，旧会话的 `.workbuddy/memory/` 不会跟过来。新会话第一句建议：

```
读 max-mcp/README.md 和 max-mcp/MIGRATION.md，了解这个项目再动手。
```

README 里已经有：架构、13 个踩过的坑、诊断方法、跨版本说明、API 反射用法、
Credits、Known limitations、TODO。**足够冷启动，不需要旧会话上下文。**

如果想让新会话继承更细的过程记录，把旧目录的
`.workbuddy/memory/2026-10-30.md` 复制到新 workspace 的 `.workbuddy/memory/` 下。

---

## 6. 当前状态（2026-10-30）

| 项 | 值 |
|---|---|
| bridge 版本 | **0.3.8**（源码目录已更新，需重新 install 并重启 Max 生效） |
| 支持版本 | 2016（实测）、2025（实测）、2027 及以下（理论兼容，未验） |
| 端口规则 | `47100 + (年份 - 2016)` → 2016=47100、2025=47109 |
| 工具数 | 15 个 MCP 工具 + `maxctl` CLI |
| 本地文档 | 2025 语料 2591 页 / 1.3 MB（`max_doc` 用）；2016 正文不可得 |
| API 快照 | 2016 = 2066 类 / 37,051 成员；2025 = 1748 类 / 15,585 成员 |
| 跨版本差异 | 仅 2016 有 732 类、仅 2025 有 414 类、153 类成员漂移 |
| 验证入口 | `python tools\check.py`（ruff + 格式 + unittest + 冒烟 + 协议回归，单退出码） |
| git | 已推送至 `https://github.com/LxXStudio2025/3dsmax-mcp-LxX`（public，MIT） |
| 已知限制 | 同版本多开抢占端口；POST 无超时（WebClient 无此设置） |

`0.3.8` 的改动**需要重新执行 `tools\install.py --install` 并重启 Max**，
新会话第一次连上时如果 `max_status` 里的 `bridge_version` 不是 `0.3.8`，说明加载的还是旧脚本。
