"""MCP server entry point for 3ds Max.

One codebase serves every release. The release year is derived from the bridge
port (`47100 + (year - 2016)`) so that a 2016 server and a 2025 server running
side by side do not present identical names and identical descriptions -- which
is exactly how an agent ends up editing the wrong session.
"""

from __future__ import annotations

from . import tools as _tools
from .compat import MCPServer

INSTRUCTIONS = """\
This server drives a live Autodesk 3ds Max 2016 session on the same machine.

How it is wired: this process opens a loopback HTTP endpoint; a MAXScript timer
inside Max polls it every 100 ms, executes what it receives on Max's main
thread, and posts the result back. Consequences you must respect:

1. Max must be running with `max_mcp_bridge.ms` loaded, otherwise every call
   fails with "3ds Max never contacted the bridge".
2. Max executes one thing at a time on its UI thread. A long script freezes
   Max's UI until it finishes and cannot be cancelled from here -- the human
   has to press ESC in Max. Keep individual calls under a few seconds.
   If a call times out, THE SCRIPT KEEPS RUNNING inside Max: the tool returns
   while the scene is still being modified. That is the whole reason the busy /
   restarted errors say "inspect before retrying". A timeout never means
   "nothing happened".
3. The scene document is never saved: there is deliberately no save tool, and
   .max files stay untouched on disk. max_file_io does write files (export), so
   "not saved" applies to the scene, not to exports you explicitly request.

BEFORE writing any MAXScript, verify the API instead of recalling it:
  max_api "Box"                  -> members the class declares + their types
  max_api_search "VRay"          -> is that plugin/class installed here at all
  max_check ["(Box()).wirecolor", "(obj).custom"] -> what actually evaluates
Your memory of MAXScript is unreliable and version-specific. In building this
bridge, six confident assumptions were wrong (BooleanClass vs Boolean,
formattedPrint format strings, getNodeByName default args, node.layer,
byte[].Length, case on strings). Every one was caught by running it.

max_check cleans up after itself, but only for NODES IT CREATED. Probe freely
for "does this property exist" -- `(Box()).wirecolor` costs you nothing. It does
NOT undo property assignments, deletions, or file writes a probe performed, so
never point it at destructive code and assume the cleanup makes it safe.

Errors carry a classification suffix, e.g. `[max_error code=busy
retryable=false]`. Read it before retrying: code=busy and code=restarted both
mean the script MAY ALREADY HAVE RUN and changed the scene -- inspect first,
never retry blindly. code=not_connected is the only one that is safe to repeat.

Known blind spot: max_api lists only members declared on the class. Node
universals (name, pos, wirecolor, isHidden, parent, material) appear in no
property list yet work fine -- use max_check for those.

Working order that produces good results:
  max_status          -> confirm connection, units, what scene is open
  max_scene_summary   -> understand the scene before touching it
  max_query_objects   -> narrow down what you will act on (paginate, do not dump)
  max_get_object      -> inspect one node in detail
  ... act with max_set_property / max_create_object / max_delete_objects / max_file_io
  max_screenshot      -> verify the result visually
  max_eval            -> escape hatch for anything the structured tools miss
  max_python          -> Python engine, only for logic (7-180x faster than
                         MAXScript at pure computation, 2-9x SLOWER at scene I/O)

Units are whatever the scene uses (max_status reports them); do not assume
centimetres. getNodeByName is an exact, case-sensitive match on node name.
"""


# Port -> release year. Keep in sync with max_mcp_bridge.ms (__MAXMCP_PORT).
PORT_BASE = 47100
PORT_BASE_YEAR = 2016


def release_year() -> str:
    """Release year implied by the configured bridge port, or "unknown"."""
    port = _tools.BRIDGE.port
    offset = port - PORT_BASE
    if 0 <= offset <= 30:
        return str(PORT_BASE_YEAR + offset)
    return "unknown"


def build() -> MCPServer:
    year = release_year()
    port = _tools.BRIDGE.port

    header = (
        f"This server is bound to bridge port {port}, i.e. 3ds Max {year}.\n"
        f"If another 3ds Max server is also enabled (a different port / release),\n"
        f"check `max_status` -> max_version before acting, and never assume the two\n"
        f"sessions hold the same scene.\n\n"
    )
    body = INSTRUCTIONS.replace("Autodesk 3ds Max 2016", f"Autodesk 3ds Max {year}")
    body = body.replace(
        "max_status          -> confirm connection, units, what scene is open",
        "max_status          -> confirm connection, VERSION, units, what scene is open",
    )

    name = f"3dsmax{year}" if year != "unknown" else "3dsmax"
    mcp = MCPServer(name, instructions=header + body)
    _tools.register(mcp)
    return mcp


def main() -> None:
    _tools.BRIDGE.start()
    try:
        build().run()
    finally:
        _tools.BRIDGE.stop()


if __name__ == "__main__":
    main()
