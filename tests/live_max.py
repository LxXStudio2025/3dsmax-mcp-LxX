"""Live test against a real 3ds Max 2016 session.

Starts the bridge, waits for the MAXScript poller inside Max to connect, then
walks through every tool once. Run this while Max is starting up -- the bridge
does not need to exist first, the poller retries every 100 ms.
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

# Without this, piping the output through anything (tail, tee) buffers
# everything until exit and the run looks hung.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from max_mcp import tools  # noqa: E402

WAIT_S = 240


def wait_for_max() -> bool:
    print(f"waiting up to {WAIT_S}s for Max to poll {tools.BRIDGE.host}:{tools.BRIDGE.port} ...")
    tools.BRIDGE.start()
    t0 = time.time()
    last_note = 0
    while time.time() - t0 < WAIT_S:
        if tools.BRIDGE.online:
            print(f"  connected after {time.time() - t0:.1f}s")
            return True
        time.sleep(0.5)
        elapsed = int(time.time() - t0)
        if elapsed and elapsed % 10 == 0 and elapsed != last_note:
            last_note = elapsed
            print(f"  ... still waiting ({elapsed}s)")
    return False


def step(name, fn, *a, **kw):
    print(f"\n=== {name} ===")
    try:
        out = fn(*a, **kw)
        print(out)
        return out
    except Exception as exc:
        print(f"!! {type(exc).__name__}: {exc}")
        return None


def main() -> int:
    if not wait_for_max():
        print("FAIL: Max never connected. Is max_mcp_bridge.ms in the startup folder?")
        return 1

    step("max_status", tools.max_status)
    step("max_eval 1+1", tools.max_eval, "1+1")
    step("max_eval string+unicode", tools.max_eval, '"中文测试"')
    step("max_eval point3", tools.max_eval, "[10,20,30]")
    step("max_eval array", tools.max_eval, "#(1, 2.5, \"x\", true, undefined)")

    step("max_create_object box", tools.max_create_object, "box", "MCP_TestBox",
         [0, 0, 0], {"w": 50, "l": 50, "h": 50})
    step("max_query_objects", tools.max_query_objects, "MCP_*", "", "", 10, 0)
    step("max_get_object", tools.max_get_object, "MCP_TestBox")

    step("max_scene_summary", tools.max_scene_summary, 8)
    step("max_screenshot", tools.max_screenshot, False, 1280, 720)

    step("max_delete_objects", tools.max_delete_objects, ["MCP_TestBox"])
    print("\nDONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
