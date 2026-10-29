"""Probe the MCP stdio surface against the real Max that is already polling.

No fake client here -- the server subprocess owns the bridge port and the live
MAXScript poller inside Max connects to it, so this exercises the exact path a
real MCP client would use.
"""

from __future__ import annotations

import asyncio
import os
import sys

PORT = os.environ.get("MAXMCP_PORT", "47100")


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "max_mcp.server"],
        env={**os.environ, "MAXMCP_PORT": PORT, "PYTHONPATH": "src"},
    )

    failures = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
            print("server:", info.name, "v" + (info.version or "?"))

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"tools ({len(names)}):", ", ".join(names))

            result = await session.call_tool("max_status", {})
            text = "".join(
                c.text for c in result.content if getattr(c, "type", "") == "text"
            )
            print("max_status ->", text[:260])
            if '"max_version": "2016"' not in text.replace("'", '"'):
                print("!! unexpected status payload")
                failures += 1

            # an image result should come back as an image content block
            pic = await session.call_tool("max_screenshot", {"render": False})
            kinds = [getattr(c, "type", "?") for c in pic.content]
            print("max_screenshot content types:", kinds)
            if "image" not in kinds:
                print("!! screenshot did not return an image block")
                failures += 1

    print("\nRESULT:", "PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
