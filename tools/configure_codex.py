"""Update only this project's Codex MCP entries; preserve unrelated TOML."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import tomlkit

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def patch_config(text: str, root: Path, versions: list[str]) -> str:
    """Update known fields in a TOML document without reconstructing user tables."""
    document = tomlkit.parse(text)
    entries = document.get("mcp_servers")
    if entries is None:
        entries = tomlkit.table()
        document["mcp_servers"] = entries
    root = root.resolve()
    for year in dict.fromkeys(versions):
        if not re.fullmatch(r"20\d{2}", year) or not 2016 <= int(year) <= 2099:
            raise ValueError("version must be a release year from 2016 through 2099")
        name = "3dsmax" + year
        entry = entries.get(name)
        if entry is None:
            entry = tomlkit.table()
            entries[name] = entry
        else:
            args = entry.get("args", [])
            owned = "max_mcp.server" in args or any(
                str(arg).replace("\\", "/").endswith("/tools/run_server.py")
                for arg in args
            )
            if not owned:
                raise ValueError(f"refusing to replace unrelated MCP entry {name}")
        desired = {
            "command": str(root / ".venv/Scripts/python.exe"),
            "args": [str(root / "tools/run_server.py")],
            "cwd": str(root),
        }
        for key, value in desired.items():
            if entry.get(key) != value:
                entry[key] = value
        entry.setdefault("startup_timeout_sec", 30)
        entry.setdefault("tool_timeout_sec", 660)
        env = entry.get("env")
        if env is None:
            env = tomlkit.table()
            entry["env"] = env
        for key, value in {
            "MAXMCP_PORT": str(47100 + int(year) - 2016),
            "PYTHONUTF8": "1",
        }.items():
            if env.get(key) != value:
                env[key] = value
    updated = tomlkit.dumps(document)
    tomllib.loads(updated)
    return updated


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--version",
        action="append",
        default=[],
        help="repeat for each installed Max release",
    )
    ap.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        / "config.toml",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.version:
        from install import discover

        args.version = [r["version"] for r in discover()]
    if not args.version:
        ap.error("no Max installation found; pass --version")
    text = args.config.read_text(encoding="utf-8-sig") if args.config.exists() else ""
    updated = patch_config(text, root, args.version)
    if updated == text:
        print("MCP config already current")
        return 0
    if args.dry_run:
        print("Would update: " + ", ".join("3dsmax" + y for y in args.version))
        return 0
    args.config.parent.mkdir(parents=True, exist_ok=True)
    if args.config.exists():
        backup = args.config.with_name(
            args.config.name
            + ".maxmcp-"
            + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            + ".bak"
        )
        shutil.copy2(args.config, backup)
    else:
        backup = None
    fd, staged_name = tempfile.mkstemp(
        prefix=args.config.name + ".", suffix=".tmp", dir=args.config.parent
    )
    os.close(fd)
    staged = Path(staged_name)
    try:
        staged.write_text(updated, encoding="utf-8")
        current = (
            args.config.read_text(encoding="utf-8-sig") if args.config.exists() else ""
        )
        if current != text:
            raise RuntimeError(
                "config changed during update; retry after concurrent edits finish"
            )
        os.replace(staged, args.config)
    finally:
        staged.unlink(missing_ok=True)
    print("Updated " + str(args.config) + "; entries: " + ", ".join(args.version))
    if backup:
        print("Backup: " + str(backup))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
