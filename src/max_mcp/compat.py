"""Shim over the two MCP Python SDK generations.

mcp 1.x exposed `FastMCP` from `mcp.server.fastmcp`; 2.x renamed it to
`MCPServer` under `mcp.server.mcpserver` and made `fastmcp` an error stub.
Everything this project uses (the `.tool()` decorator, `run()`, `Image`) is
signature-compatible across both, so one import swap covers it.
"""

from __future__ import annotations

try:  # mcp >= 2.0
    from mcp.server.mcpserver import Image as Image
    from mcp.server.mcpserver import MCPServer as MCPServer
    from mcp.server.mcpserver.exceptions import ToolError as ToolError

    SDK_MAJOR = 2
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[assignment]
    from mcp.server.fastmcp import Image as Image  # type: ignore[assignment]
    from mcp.server.fastmcp.exceptions import ToolError as ToolError  # type: ignore[assignment]

    SDK_MAJOR = 1

__all__ = ["MCPServer", "Image", "ToolError", "SDK_MAJOR"]
