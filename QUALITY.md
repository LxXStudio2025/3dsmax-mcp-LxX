# 源码质量与日常验证

当前版本：0.3.8。

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

这些证据覆盖已执行路径，不代表所有插件、导入导出格式、渲染器、Max 版本或所有并发时序都已穷尽。Max 2025 仍需运行验收。
