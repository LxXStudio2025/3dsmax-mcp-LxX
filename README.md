# max-mcp

让 AI 直接操作运行中的 **3ds Max** 的 MCP 服务器 + CLI。

支持 2016 起的各版本，已在 2016 与 2025 实测通过，两个版本可同时运行、互不干扰。
Max 端是纯 MAXScript，不依赖内置 Python、pymxs 或 PySide6。

---

## 安装

**1. 装 Python 包**（需要 Python 3.10+）

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

**2. 装进 Max**

```bash
python tools\install.py --list       # 看装了哪些版本
python tools\install.py --install    # 全部装上
```

它会自动找到安装目录，写两个文件：

```
<Max>\scripts\maxmcp\max_mcp_bridge.ms      ← 真身
<Max>\scripts\startup\start_maxmcp.ms       ← 启动桩，只负责加载真身
```

重启 Max 即生效。不需要手动往 startup 里复制任何东西。

**3. 配 MCP**

在客户端配置里加一条：

```json
"3dsmax2025": {
  "command": "<项目路径>\\.venv\\Scripts\\python.exe",
  "args": ["-m", "max_mcp.server"],
  "env": { "MAXMCP_PORT": "47109" }
}
```

`MAXMCP_PORT` 对应要连的版本，见下面的端口规则。改完需要刷新客户端并新开会话。

---

## 用法

```bash
maxctl status                        # 连接、版本、场景、单位
maxctl doctor                        # 连不上时先跑这个
maxctl eval "1+1"                    # 执行 MAXScript
maxctl eval - < script.ms            # 从 stdin 读整段脚本
maxctl query --pattern "SM_*"        # 列节点
maxctl get Box001                    # 单节点详情
maxctl set Box001 --prop wirecolor --value 255,0,0
maxctl create box --name Test --param w=50 --param h=20
maxctl shot --out shot.png           # 抓视口，--render 出渲染
maxctl repl                          # 交互式
maxctl doc "polyop"                  # 搜本地 MAXScript 参考
```

任何子命令加 `--json` 出原始 JSON；退出码 0 成功 / 1 工具错误 / 2 用法错误。

---

## 工具

MCP 侧暴露 15 个工具，`maxctl` 是同一套能力在命令行上的入口。

| 工具 | 作用 |
|---|---|
| `max_status` | 连接、版本、单位、场景概况。每次会话先调它 |
| `max_scene_summary` | 对象统计、图层、缺贴图 |
| `max_query_objects` | 按名称/类型/图层查节点 |
| `max_get_object` | 单节点详情（变换、修改器、材质、包围盒） |
| `max_set_property` | 批量改属性 |
| `max_create_object` | 建基础体或导入几何 |
| `max_delete_objects` | 删节点 |
| `max_screenshot` | 抓视口 / 快速渲染，返回图像 |
| `max_file_io` | 导入 / 导出 / 合并 |
| `max_eval` | 执行 MAXScript（万能口） |
| `max_python` | 在 Max 内跑 Python（逻辑用，场景操作别用） |
| `max_api` | 查某个类声明了哪些成员及类型 |
| `max_api_search` | 在全量 API 快照里搜类名/属性 |
| `max_check` | 试跑表达式，确认属性能不能用 |
| `max_doc` | 搜本地 MAXScript 参考 |

**写脚本前先查 API**：`max_api "Editable_Poly"` 看有什么，
`max_check ["(Box()).wirecolor"]` 确认能用。文档和记忆都可能过时，运行中的进程不会。

---

## 端口规则

```
port = 47100 + (发布年份 - 2016)
```

| 版本 | 端口 |
|---|---|
| 2016 | 47100 |
| 2025 | 47109 |
| 2027 | 47111 |

端口只按年份区分，所以同一个版本开两个 Max 会冲突。需要多开时自行指定
`MAXMCP_PORT`，保证两个实例不同。

---

## 连不上时

按层排查：

| 现象 | 说明 | 看哪里 |
|---|---|---|
| `MaxNotConnected` | Max 没开，或 bridge 没加载 | `%TEMP%\maxmcp-launcher-<年份>.txt` 存不存在 |
| 端口无任何记录 | bridge 没在轮询 | `%TEMP%\maxmcp-boot-<年份>.log` 有没有 `boot finished` |
| 有 `LISTENING` 无 `ESTABLISHED` | MCP server 起了但 Max 没连上 | 端口是否对得上 |
| 工具列表里没有 `max_*` | 客户端没启用，或会话没刷新 | 重新启用 + 新开会话 |

Max 里有两个菜单项（`Customize > Customize User Interface` 搜 `max-mcp`）：

- **MCP Status** — 端口、是否在跑、已服务任务数、最后的错误
- **MCP Self Test** — 验证 JSON 编码器和 timer 状态

设环境变量 `MAXMCP_AUTOSTART=0` 可让 bridge 只加载不启动，需要时点
`MCP Bridge` 手动开。

---

## 许可

[MIT License](LICENSE) —— 仓库根目录有独立文件（便于 GitHub 识别授权），下面附一份全文。

<details>
<summary>MIT License 全文</summary>

```
Copyright (c) 2026 LxXStudio

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

</details>
