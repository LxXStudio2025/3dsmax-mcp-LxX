"""End-to-end smoke test with a fake Max client -- no 3ds Max required.

It impersonates the MAXScript timer: polls /poll, reads the task id from the
response header, and posts a canned JSON body back. That exercises the whole
transport (HTTP framing, task queue, header plumbing, flat-record zipping)
without Max in the loop.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request

sys.path.insert(0, "src")

from max_mcp import tools  # noqa: E402
from max_mcp.bridge import Bridge  # noqa: E402

BASE = f"http://127.0.0.1:{tools.BRIDGE.port}"

SCRIPTS_SEEN: list[str] = []
stop = threading.Event()


def fake_max() -> None:
    """Stand in for the MAXScript poller."""
    while not stop.is_set():
        try:
            with urllib.request.urlopen(f"{BASE}/poll", timeout=1.0) as resp:
                tid = resp.headers.get("X-Task-Id", "-")
                body = resp.read().decode("utf-8")
        except Exception:
            time.sleep(0.1)
            continue

        if tid == "-" or not body:
            time.sleep(0.05)
            continue

        SCRIPTS_SEEN.append(body)

        # Answer as if MAXScript had executed the snippet.
        if "__rows" in body or '"items"' in body:
            payload = {
                "ok": True,
                "data": [
                    "count", 2,
                    "truncated", False,
                    "offset", 0,
                    "items", [["name", "Box001", "class", "Box"], ["name", "Sphere002", "class", "Sphere"]],
                ],
            }
        elif "bbox_min" in body:
            payload = {"ok": True, "data": ["found", True, "name", "Box001", "children", ["a", "b"]]}
        elif body.lstrip().startswith("throw"):
            payload = {"ok": False, "error": "-- Runtime error: deliberate"}
        else:
            payload = {"ok": True, "data": ["bridge_version", "0.1.0", "max_version", "2016"]}

        raw = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{BASE}/result", data=raw, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Task-Id", tid)
        try:
            urllib.request.urlopen(req, timeout=2.0).read()
        except Exception as exc:  # pragma: no cover
            print("post failed:", exc)


def main() -> int:
    tools.BRIDGE.start()
    t = threading.Thread(target=fake_max, daemon=True)
    t.start()
    time.sleep(0.3)

    failures = 0

    # 1. bridge reports online
    print("online:", tools.BRIDGE.online)
    if not tools.BRIDGE.online:
        print("FAIL: bridge not online")
        failures += 1

    # 2. plain eval round-trip -- max_eval returns the raw value, never zipped
    out = tools.max_eval("1+1")
    print("max_eval ->", out)
    if not isinstance(out, list) or "0.1.0" not in out:
        print("FAIL: max_eval payload")
        failures += 1

    # 3. query objects expands item rows into dicts
    q = tools.max_query_objects(name_pattern="Box*", limit=2)
    print("max_query_objects ->", q)
    if q.get("count") != 2:
        print("FAIL: query count")
        failures += 1
    if not isinstance(q.get("items"), list) or not isinstance(q["items"][0], dict):
        print("FAIL: query items not dicts")
        failures += 1

    # 4. get object
    g = tools.max_get_object("Box001")
    print("max_get_object ->", g)
    if g.get("name") != "Box001":
        print("FAIL: get_object")
        failures += 1

    # 5. error propagation
    try:
        tools.max_eval('throw "boom"')
        print("FAIL: error was swallowed")
        failures += 1
    except Exception as exc:
        print("max_eval error path ->", type(exc).__name__, str(exc)[:80])

    # 6. script literal escaping survives the trip
    tools.max_set_property(["A"], "name", 'he said "hi"\\n')
    if not any('he said \\"hi\\"' in s for s in SCRIPTS_SEEN):
        print("FAIL: string escaping lost:", SCRIPTS_SEEN[-1][:200])
        failures += 1
    else:
        print("escaping ok")

    stop.set()
    print("\nRESULT:", "PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
