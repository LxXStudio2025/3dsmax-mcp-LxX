"""Live MCP validation. Use an idle port and a Max instance configured for it.

The default mode reads status and captures the viewport. --roundtrip additionally
requires --expected-pid and an initially empty, dedicated Max scene.
All detailed results go to a uniquely named directory outside the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import socket
import struct
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def decode_result(result):
    """Support MCP SDK 1.x aliases and 2.x snake_case field names."""
    data = result.model_dump()
    if data.get("is_error", data.get("isError", False)):
        messages = [
            item["text"]
            for item in data.get("content", [])
            if item.get("type") == "text"
        ]
        raise RuntimeError("; ".join(messages))
    structured = data.get("structured_content", data.get("structuredContent"))
    if structured is not None:
        return structured
    text = [
        item["text"] for item in data.get("content", []) if item.get("type") == "text"
    ]
    if len(text) == 1:
        try:
            return json.loads(text[0])
        except ValueError:
            return text[0]
    return text


def error_summary(error: BaseException) -> str:
    """Keep the underlying failure when SDK task groups wrap tool errors."""
    children = getattr(error, "exceptions", ())
    if children:
        return "; ".join(dict.fromkeys(error_summary(child) for child in children))
    return f"{type(error).__name__}: {error}"


async def wait_for_status(call, startup_timeout: float) -> dict:
    """Only initial read-only not_connected failures are safe to retry."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + startup_timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RuntimeError("Max did not connect within the startup timeout")
        try:
            return await asyncio.wait_for(call("max_status", {}), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Max did not connect within the startup timeout"
            ) from exc
        except RuntimeError as exc:
            if "[max_error code=not_connected retryable=true]" not in str(exc):
                raise
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(
                    "Max did not connect within the startup timeout"
                ) from exc
            await asyncio.sleep(min(0.25, remaining))


def assert_identity(status: dict, version: str, pid: int | None, port: int) -> None:
    if str(status.get("max_version")) != version or status.get("port") != port:
        raise RuntimeError(f"Unexpected Max identity: {status}")
    if pid is not None and status.get("pid") != pid:
        raise RuntimeError(f"Expected Max PID {pid}, received {status.get('pid')}")


def ensure_idle_port(port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(
                f"Port {port} is occupied. Keep that connector running; choose a separate test port and matching Max instance."
            )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MAXMCP_PORT", "47100"))
    )
    parser.add_argument("--expected-version", default="2016")
    parser.add_argument("--expected-pid", type=int)
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=30.0,
        help="seconds to wait for the first Max connection",
    )
    parser.add_argument(
        "--roundtrip",
        action="store_true",
        help="test OBJ export/import, MAX merge and a 128px render in an empty dedicated scene",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="parent directory for a unique run folder (default: system temp)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not math.isfinite(args.startup_timeout) or args.startup_timeout <= 0:
        parser.error("--startup-timeout must be a finite positive number")
    if args.roundtrip and args.expected_pid is None:
        parser.error(
            "--roundtrip requires --expected-pid to identify the dedicated test instance"
        )
    return args


async def validate(args, output: Path) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from max_mcp.maxscript import mxs_str

    report = {"passed": False, "port": args.port, "output": str(output)}
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tools/run_server.py")],
        cwd=str(output),
        env={**os.environ, "MAXMCP_PORT": str(args.port), "PYTHONUTF8": "1"},
    )
    try:
        with (output / "stderr.txt").open("w", encoding="utf-8") as stderr:
            async with stdio_client(parameters, errlog=stderr) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    report["tools"] = [tool.name for tool in tools.tools]
                    sequence = 0

                    async def call(name, arguments):
                        nonlocal sequence
                        sequence += 1
                        result = await session.call_tool(name, arguments)
                        (output / f"{sequence:02d}-{name}.json").write_text(
                            result.model_dump_json(indent=2), encoding="utf-8"
                        )
                        return decode_result(result)

                    async def screenshot(render=False):
                        result = await session.call_tool(
                            "max_screenshot",
                            {"render": render, "width": 128, "height": 128},
                        )
                        decode_result(result)
                        images = [
                            item
                            for item in result.model_dump()["content"]
                            if item["type"] == "image"
                        ]
                        if not images:
                            raise RuntimeError("Screenshot returned no image")
                        raw = base64.b64decode(images[0]["data"])
                        if raw[:8] != b"\x89PNG\r\n\x1a\n":
                            raise RuntimeError("Screenshot did not return PNG data")
                        size = struct.unpack(">II", raw[16:24])
                        if render and size != (128, 128):
                            raise RuntimeError(f"Render size mismatch: {size}")
                        (
                            output / ("render.png" if render else "viewport.png")
                        ).write_bytes(raw)
                        return list(size)

                    status = await wait_for_status(call, args.startup_timeout)
                    assert_identity(
                        status, args.expected_version, args.expected_pid, args.port
                    )
                    report["initial_status"] = status
                    report["viewport_size"] = await screenshot()
                    if args.roundtrip:
                        if status["objects"] != 0 or status.get("scene_name"):
                            raise RuntimeError(
                                "Roundtrip requires an empty unsaved dedicated scene"
                            )
                        marker = "__MCP_VALID_" + uuid.uuid4().hex[:10]
                        owned_names = {marker}
                        may_have_mutated = False
                        try:
                            # Mark before dispatch: a timeout does not imply no scene mutation.
                            may_have_mutated = True
                            report["create"] = await call(
                                "max_create_object",
                                {
                                    "kind": "box",
                                    "name": marker,
                                    "pos": [1, 2, 3],
                                    "params": {"w": 4, "l": 5, "h": 6},
                                },
                            )
                            await call(
                                "max_set_property",
                                {
                                    "names": [marker],
                                    "prop": "pos",
                                    "value": [11, 12, 13],
                                },
                            )
                            node = await call("max_get_object", {"name": marker})
                            if node.get("pos") != [11, 12, 13]:
                                raise RuntimeError("Position readback mismatch")
                            checks = await call(
                                "max_check",
                                {
                                    "expressions": ["saveNodes", "getNodeByName"],
                                    "timeout_seconds": 15,
                                },
                            )
                            if not all(item["ok"] for item in checks["results"]):
                                raise RuntimeError(
                                    "Required fixture APIs are unavailable"
                                )
                            await call(
                                "max_eval",
                                {
                                    "code": "(select (getNodeByName "
                                    + mxs_str(marker)
                                    + "); true)"
                                },
                            )
                            obj_path, max_path = (
                                output / "roundtrip.obj",
                                output / "roundtrip.max",
                            )
                            report["obj_export"] = await call(
                                "max_file_io",
                                {
                                    "action": "export",
                                    "path": str(obj_path),
                                    "selected_only": True,
                                },
                            )
                            if not obj_path.is_file() or obj_path.stat().st_size == 0:
                                raise RuntimeError("OBJ exporter created no data")
                            await call(
                                "max_eval",
                                {
                                    "code": "(saveNodes #((getNodeByName "
                                    + mxs_str(marker)
                                    + ")) "
                                    + mxs_str(str(max_path))
                                    + ")"
                                },
                            )
                            if not max_path.is_file():
                                raise RuntimeError("MAX fixture creation failed")
                            await call("max_delete_objects", {"names": [marker]})
                            report["obj_import"] = await call(
                                "max_file_io",
                                {"action": "import", "path": str(obj_path)},
                            )
                            imported = await call("max_query_objects", {"limit": 100})
                            names = [node["name"] for node in imported["items"]]
                            owned_names.update(name for name in names if marker in name)
                            if (
                                not names
                                or any(name not in owned_names for name in names)
                                or imported.get("truncated")
                            ):
                                raise RuntimeError(
                                    "Unexpected import nodes; inspect before cleanup"
                                )
                            await call("max_delete_objects", {"names": names})
                            report["max_merge"] = await call(
                                "max_file_io",
                                {"action": "merge", "path": str(max_path)},
                            )
                            merged = await call("max_query_objects", {"limit": 100})
                            if (
                                merged["count"] != 1
                                or merged["items"][0]["name"] != marker
                            ):
                                raise RuntimeError("MAX merge readback mismatch")
                            report["render_size"] = await screenshot(render=True)
                        finally:
                            if may_have_mutated:
                                try:
                                    current = await call("max_status", {})
                                    assert_identity(
                                        current,
                                        args.expected_version,
                                        args.expected_pid,
                                        args.port,
                                    )
                                    remaining = await call(
                                        "max_query_objects", {"limit": 100}
                                    )
                                    known = [
                                        node["name"]
                                        for node in remaining["items"]
                                        if node["name"] in owned_names
                                    ]
                                    if known:
                                        await call(
                                            "max_delete_objects", {"names": known}
                                        )
                                    report["final_status"] = await call(
                                        "max_status", {}
                                    )
                                    if report["final_status"]["objects"]:
                                        raise RuntimeError(
                                            "Scene contains remaining nodes; left unknown objects untouched"
                                        )
                                except Exception as exc:
                                    report["cleanup_error"] = str(exc)
                                    raise
                    report["passed"] = True
    except Exception as exc:
        report["error"] = error_summary(exc)
        raise
    finally:
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main(argv=None) -> int:
    args = parse_args(argv)
    ensure_idle_port(args.port)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="max-mcp-live-", dir=args.output_dir))
    try:
        asyncio.run(validate(args, output))
    except Exception as exc:
        print(f"FAIL: {error_summary(exc)}\nEvidence: {output}", file=sys.stderr)
        return 1
    print(f"PASS: live MCP validation. Evidence: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
