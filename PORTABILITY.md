# 安装、迁移和路径规则

适用版本：0.3.8。入口脚本始终相对自己的位置定位项目，不包含作者电脑盘符。

## 新电脑

安装 Python 3.10+ 和目标 3ds Max，复制整个项目，在项目目录运行：

```powershell
.\setup.ps1 -ConfigureCodex
```

脚本创建项目自己的 `.venv`，安装依赖，从注册表与常用安装目录发现 Max，安装桥接，并更新当前用户 Codex 配置。注册表中已不存在的 Max 安装不会遮挡新的有效目录。

## Max 安装目录变动

若整个 Max 文件夹连同 `scripts` 一起移动，启动入口从自己的 `startup` 目录定位 `../maxmcp/max_mcp_bridge.ms`，不依赖旧安装路径。新装或未拷贝 scripts 时重新运行安装。若注册表尚未更新且目录不在自动发现范围，指定目录：

```powershell
.\setup.ps1 -Version 2016 -MaxDir 'D:\Autodesk\3ds Max 2016' -ConfigureCodex
```

这里的路径是使用者显式输入的示例，不会成为项目默认值。自动发现覆盖本机已有盘符的常见 Max 安装布局，不递归扫描整盘。

## 项目目录变动

Codex 必须有一个可找到启动程序的入口，因此用户配置会保存当前机器的项目路径。移动项目后，在新目录运行：

```powershell
.\setup.ps1 -RebuildVenv -ConfigureCodex
```

`.venv` 不保证可以跨路径或跨电脑直接使用。重建选项先把旧环境重命名为 `.venv.backup-时间戳`，再创建新环境。配置更新器备份 `config.toml`，只更新识别为本项目的 `3dsmax<年份>` 条目，保留其他 MCP、项目和已设置的 enabled/env/timeout。刷新或重启客户端连接后生效。

只刷新某版本配置、不安装或启动 Max：

```powershell
.\.venv\Scripts\python.exe .\tools\configure_codex.py --version 2016
```

自定义 Python 可通过 `-Python 'C:\Python312\python.exe'` 指定。若写入 Max 安装目录被 Windows 拒绝，请在具有该目录写入权限的终端运行安装。

## 资源文件路径

导入、导出、合并的相对路径在当前服务工作目录解析一次，再把同一个绝对路径传给 Max，防止 Python 和 Max 各自工作目录不同导致校验 A、操作 B。安装配置将 `cwd` 设为当前项目目录；它可由使用者自行调整。绝对路径仅在运行期生成，不会写入源码。

## 验证边界

2026-10-30：22 项新增离线回归、原有 smoke/regress、编译与差异空白检查通过。Max 2016 已完成真实 MCP stdio 创建/移动/读回/删除、反射、Python、截图与 Unicode 测试；相对 launcher 重载及 64 位整数返回通过。Max 2025 安装已更新，运行时未验收。
