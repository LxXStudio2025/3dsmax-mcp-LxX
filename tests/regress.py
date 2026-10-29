"""Regression tests for the 2026-10 hardening. No 3ds Max required.

Each test pins down a bug that was actually hit on a live session, so a future
change that reintroduces it fails here instead of in someone's scene:

  1. MaxError must derive from ToolError      -- otherwise the MCP SDK hides the
     message and the agent sees only "Error executing tool <name>"
  2. busy vs restarted is decided by whether Max picked the task up, never by
     poll recency (a busy Max cannot poll at all)
  3. the grace window turns "finished but POST failed" into success
  4. max_check cleans up through #nodeCreated and reports leftovers
  5. max_file_io refuses to overwrite, and refuses a directory as a path
  6. screenshot/max_python temp files are removed

Run: .venv\\Scripts\\python -u tests\\regress.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, "src")

from max_mcp import tools  # noqa: E402
from max_mcp.bridge import Bridge, MaxBusy, MaxError, MaxRestarted  # noqa: E402
from max_mcp.compat import ToolError  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


# ---------------------------------------------------------------------------
#  1. exception semantics
# ---------------------------------------------------------------------------


def test_error_semantics() -> None:
    print("\n[1] error classification reaches the client")
    for cls, code, retryable in (
        (MaxBusy, "busy", "false"),
        (MaxRestarted, "restarted", "false"),
        (MaxError, "error", "false"),
    ):
        exc = cls("boom")
        check(f"{cls.__name__} is ToolError", isinstance(exc, ToolError))
        check(
            f"{cls.__name__} carries code={code}",
            f"[max_error code={code} retryable={retryable}]" in str(exc),
            str(exc),
        )
    check("no dangling leading newline", not str(MaxError("")).startswith("\n"))


# ---------------------------------------------------------------------------
#  2 & 3. busy / restarted / grace window, driven by a scripted fake Max
# ---------------------------------------------------------------------------


class FakeMax(threading.Thread):
    """Impersonates the MAXScript timer.

    mode:
      "answer"   take the task, answer immediately
      "swallow"  take the task, never answer          -> MaxBusy
      "late"     take the task, answer after delay    -> grace window
      "idle"     poll without taking anything          -> MaxRestarted
    """

    def __init__(self, port: int, mode: str, delay: float = 0.0) -> None:
        super().__init__(daemon=True)
        self.base = f"http://127.0.0.1:{port}"
        self.mode = mode
        self.delay = delay
        self.stop_flag = threading.Event()

    def run(self) -> None:
        if self.mode == "idle":
            # One empty poll so the bridge's cold-start check passes, then the
            # poller vanishes. This is what a crashed Max looks like from the
            # server side: last_poll gets older, the queue is never drained.
            for _ in range(40):
                if self.stop_flag.is_set():
                    return
                try:
                    with urllib.request.urlopen(f"{self.base}/poll", timeout=0.5) as resp:
                        tid = resp.headers.get("X-Task-Id", "-")
                        resp.read()
                except Exception:
                    time.sleep(0.05)
                    continue
                if tid == "-":
                    return
            return

        while not self.stop_flag.is_set():
            try:
                with urllib.request.urlopen(f"{self.base}/poll", timeout=0.5) as resp:
                    tid = resp.headers.get("X-Task-Id", "-")
                    resp.read()
            except Exception:
                time.sleep(0.05)
                continue
            if tid == "-":
                time.sleep(0.05)
                continue
            if self.mode == "swallow":
                continue
            if self.mode == "late":
                time.sleep(self.delay)
            payload = json.dumps({"ok": True, "data": "answered"}).encode()
            req = urllib.request.Request(f"{self.base}/result", data=payload, method="POST")
            req.add_header("X-Task-Id", tid)
            req.add_header("Content-Type", "application/json")
            try:
                urllib.request.urlopen(req, timeout=2.0).read()
            except Exception:
                pass


def run_case(port: int, mode: str, timeout: float, delay: float = 0.0):
    """Start a bridge + fake Max, make one call, return (result, exception, secs)."""
    br = Bridge(port=port)
    br.start()
    fake = FakeMax(port, mode, delay)
    fake.start()
    time.sleep(0.25)

    out: dict = {}

    def caller() -> None:
        t0 = time.time()
        try:
            out["value"] = br.call("1+1", timeout=timeout)
        except BaseException as exc:  # noqa: BLE001 - we assert on the type
            out["exc"] = exc
        out["secs"] = time.time() - t0

    th = threading.Thread(target=caller)
    th.start()
    th.join()
    fake.stop_flag.set()
    br.stop()
    return out


def test_busy_vs_restarted() -> None:
    print("\n[2] busy vs restarted is decided by task pickup")

    r = run_case(47197, "swallow", timeout=1)
    check(
        "taken but unanswered -> MaxBusy",
        isinstance(r.get("exc"), MaxBusy),
        f"got {type(r.get('exc')).__name__}",
    )
    check("grace window was actually waited", r.get("secs", 0) >= 5.9, f"{r.get('secs'):.1f}s")

    r = run_case(47197, "idle", timeout=1)
    check(
        "never picked up -> MaxRestarted",
        isinstance(r.get("exc"), MaxRestarted),
        f"got {type(r.get('exc')).__name__}",
    )


def test_grace_window() -> None:
    print("\n[3] a result arriving inside the grace window is a success")
    # Answers 2 s after a 1 s timeout: within the 5 s grace, so no error at all.
    r = run_case(47197, "late", timeout=1, delay=2.0)
    check("late answer -> no exception", r.get("exc") is None, repr(r.get("exc")))
    check("late answer -> value returned", r.get("value") == "answered", repr(r.get("value")))


# ---------------------------------------------------------------------------
#  4. max_check cleanup contract
# ---------------------------------------------------------------------------


def test_max_check_script() -> None:
    print("\n[4] max_check tracks side effects by callback, not a scene diff")
    captured: dict = {}

    def fake_call(script: str, timeout: float = 30.0):
        captured["script"] = script
        return [[[True, ""], [False, "boom"]], ["Box001"], ["Locked1"], ""]

    original = tools.call
    tools.call = fake_call
    try:
        out = tools.max_check(["(Box()).wirecolor", "(Box()).nope"])
    finally:
        tools.call = original

    s = captured["script"]
    check("installs #nodeCreated callback", 'callbacks.addScript #nodeCreated' in s)
    check("uses notificationParam, not $", "callbacks.notificationParam()" in s)
    check("always removes the callback", s.count("callbacks.removeScripts id:#mcpProbe") == 2)
    check("verifies deletion with isValidNode", "isValidNode" in s)
    check("no whole-scene diff", "objects as array" not in s)
    check("reports removed", out["removed_side_effects"] == ["Box001"])
    check("reports leftovers", out["leftover_side_effects"] == ["Locked1"])
    check("harness_error empty on success", out["harness_error"] is None)


# ---------------------------------------------------------------------------
#  5. destructive file operations are refused before touching Max
# ---------------------------------------------------------------------------


def test_file_guard() -> None:
    print("\n[5] max_file_io refuses destructive paths up front")
    original = tools.call

    def exploding_call(*_a, **_k):
        raise AssertionError("must not reach Max")

    tools.call = exploding_call
    try:
        cases = [
            ("export over an existing file", lambda: tools.max_file_io("export", __file__)),
            ("import a directory", lambda: tools.max_file_io("import", tempfile.gettempdir())),
            ("export into a missing dir", lambda: tools.max_file_io("export", "C:/__nope__/a.fbx")),
            ("import a missing file", lambda: tools.max_file_io("import", "C:/__nope__/a.fbx")),
        ]
        for label, fn in cases:
            try:
                fn()
                check(label, False, "no error raised")
            except MaxError:
                check(label, True)
            except AssertionError:
                check(label, False, "reached Max")
    finally:
        tools.call = original

    # explicit overwrite must be allowed through (it still fails later, but not here)
    try:
        tools.max_file_io("export", __file__, overwrite=True)
    except MaxError as exc:
        check("overwrite=True is not blocked by the guard", "refusing to overwrite" not in str(exc))


# ---------------------------------------------------------------------------
#  6. temp files
# ---------------------------------------------------------------------------


def test_sweep() -> None:
    print("\n[6] stale temp files are swept once per process")
    os.makedirs(tools.SHOT_DIR, exist_ok=True)
    stale = os.path.join(tools.SHOT_DIR, "py_staletest.json")
    with open(stale, "w", encoding="utf-8") as fh:
        fh.write("{}")
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    tools._SWEPT = False  # allow a second sweep in this process
    tools._sweep_temp(max_age_hours=6)
    check("stale py_*.json removed", not os.path.exists(stale))


def main() -> int:
    test_error_semantics()
    test_busy_vs_restarted()
    test_grace_window()
    test_max_check_script()
    test_file_guard()
    test_sweep()

    print("\nRESULT:", "PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
