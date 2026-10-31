# 源码质量与日常验证

当前版本：0.3.9。

## 一条命令验证

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe .\tools\check.py
```

检查入口根据自身位置定位项目，按顺序运行 Ruff 静态检查、格式检查、unittest、模拟 Max 冒烟、原有协议回归和编译检查。任一阶段失败，整体退出码非零。无需开启 Max，也不会占用正在服务 Max 的 47100 端口。

`tests/bench_py*.py` 是在旧版 Max 的 Python 2 环境运行的基准片段，不由 Python 3 Ruff 改写。文档知识库不属于质量检查目标。性能结论需要独立实测。

## 模块职责

- `bridge.py`：监听生命周期、任务状态、超时、结果发布。任务结束操作共用状态锁，JSON 解码不占用该锁，结束后的任务不能被迟到结果复活。
- `maxscript.py`：纯 MAXScript 字符串、数值、向量编码与校验；不连接 Max，工具层复用一份实现。
- `tools.py`：保留原有 15 个 MCP 工具及调用参数，负责具体场景操作。
- `configure_codex.py`：通过 tomlkit 修改 TOML 文档，保留注释、多行字符串和未知用户字段；配置写入使用唯一临时文件，并检查生成期间是否发生并行修改。
- `tools/check.py`：离线质量检查统一入口。

## 2026-10-30 复查结果

30 项 unittest 通过；Ruff、格式、原有 smoke/regress、编译与 git diff --check 通过。

新增覆盖：多行 TOML 中的伪表头、内联/嵌套用户设置、结果解码期间停服、超时后的迟到结果、旧 HTTP 世代取任务、失效队列清理、非法 UTF8/超深 JSON、反射失败清理。

新版 Python 服务以独立 MCP stdio 进程连接真实 Max 2016，通过工具发现、创建/移动/读回/删除、反射、Python、截图和中文/控制字符传输；最终对象数 0，恢复端口 47100。

这些证据覆盖已执行路径，不代表所有插件、导入导出格式、渲染器、Max 版本或所有并发时序都已穷尽。Max 2025 的基础运行验收已于 2026-10-31 完成，具体范围见下文。


## 2026-10-31 扩展验证与修复

- 离线验证：43 项 unittest、Ruff、格式、smoke、regress、编译和 git diff --check 全部通过。
- 实际缺陷：Max 2016 不支持 `mergeMAXFile #noPrompt`。已改为 `#autoRenameDups #renameMtlDups #neverReparent quiet:true`，并通过真实 MAX 合并读回验证。
- 协议校验：拒绝 NaN、Infinity 和溢出浮点结果；错误字段只能为字符串或 null。
- 版本确认：`max_status.server_version` 返回当前进程实际加载的 Python 源码版本；`bridge_version` 返回 Max 内已加载的脚本版本。磁盘文件更新不会自动替换现有进程中的模块。
- 真实 Max 2016 0.3.9 实测：OBJ 导出/导入往返、MAX 保存测试夹具/合并、128×128 默认 Scanline 渲染、对象读回和清理通过。渲染验证检查了 PNG 格式和尺寸，未作复杂材质或画质验收。
- 最终测试实例对象数为 0，已正常退出。原有 47100 连接未停止或更改；独立测试使用 47123，结束后无监听残留。
- 客户端工具名/JSON 调用错误与服务故障分别记录；本轮实际验证通过 Python MCP SDK 的独立 stdio 客户端完成，不能据此声称当前 Codex 工具分发已修复。

### 可重复的实时验证

只读检查指定空闲端口上对应的 Max 实例：

```powershell
.\.venv\Scripts\python.exe .\tests\stdio_live.py --port 47123 --expected-version 2016 --expected-pid <测试Max进程PID>
```

执行 OBJ/MAX/渲染往返，再加 `--roundtrip`。它要求一个独立、空白、未命名场景，并必须传 `--expected-pid`；不会接管占用中的端口。可用 `--output-dir <目录>` 指定独立证据子目录的位置，默认在系统临时目录。测试不负责启动 Max：测试实例须以匹配的 `MAXMCP_PORT` 环境变量启动。

`--startup-timeout` 默认 30 秒，仅对首次只读状态查询的 `not_connected` 有限重试；`busy`、其他失败和写操作不会自动重试。SDK 的嵌套异常会保留根因，避免只留下 TaskGroup 报错。

边界：已验证本机 Max 2016 与 Max 2025 的 OBJ 插件、简单 MAX 场景和默认 Scanline 渲染；其他格式、第三方渲染器、复杂场景、画质和性能仍需单独验收。


## 2026-10-31 Max 2025 基础运行验收

- 通过独立 Python MCP SDK stdio 客户端连接端口 47109；状态验证 Max 2025、PID 8728，server_version 与 bridge_version 均为 0.3.9。
- 只读状态、视口 PNG、创建/移动/读回/删除、OBJ 导出/导入、MAX 合并、128×128 默认 Scanline 渲染均通过。
- 验证前为无文件名空白场景；最终对象数为 0。测试只保存临时夹具，没有保存用户主场景，Max 2025 保持运行。
- 2016 的 47100 监听未停止或改动。测试子进程结束后 47109 无监听；Codex 中的 2025 配置虽已启用，本次不能认定其常驻 MCP 连接已建立。
- 原始结果：%TEMP%/work/max2016-mcp-validation-20261031/max-mcp-live-gomyy28n/result.json。
- 本阶段只更新验收文档，没有改动运行代码，因此没有重复执行已通过的 43 项离线测试。
