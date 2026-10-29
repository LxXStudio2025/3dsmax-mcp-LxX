"""Loopback HTTP bridge between the MCP server and the 3ds Max 2016 MAXScript listener.

Design notes
------------
3ds Max 2016 ships Python 2.7 (often not even installed) and has no PySide6,
so the in-Max side is a pure MAXScript poller. MAXScript has a single main
thread and no usable async .NET callback path back into script code, therefore
the direction of the wire is inverted: Max *pulls* work instead of listening.

    MCP client --stdio--> mcp server --HTTP 127.0.0.1--> Max poller (GET /poll)

Wire format is deliberately dumb so the Max side never has to parse JSON:

    GET  /poll        -> 200, header X-Task-Id: <id> ("" when idle),
                         body = raw MAXScript source (utf-8)
    POST /result      -> body = {"ok": bool, "data": any, "error": str|null}
                         header X-Task-Id: <id>

Parameters are rendered into MAXScript literals on the Python side, so the
only thing the Max side must implement is a JSON *encoder*, not a parser.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .compat import ToolError


class MaxError(ToolError):
    """Base for anything the Max side reports or fails to report.

    Subclasses `ToolError` on purpose. The MCP SDK treats any other exception as
    a crash and withholds its text from the model: the client sees only
    "Error executing tool <name>", which is exactly how the carefully worded
    MaxBusy / MaxRestarted advice below used to disappear. Deriving from
    ToolError marks these as *anticipated* failures, so `str(exc)` reaches the
    agent and the server logs them without a traceback.
    """

    code = "error"
    retryable = False

    def __str__(self) -> str:  # keep the classification visible to the client
        text = super().__str__()
        return f"{text}\n[max_error code={self.code} retryable={str(self.retryable).lower()}]"


class MaxNotConnected(MaxError):
    """Max never polled the bridge: the listener script is not loaded."""

    code = "not_connected"
    retryable = True


class MaxBusy(MaxError):
    """Max is polling but did not answer in time -- long script or modal dialog.

    `retryable` is False on purpose: the script was already handed over and may
    have modified the scene. Retrying blindly can delete twice, assign a
    material twice, or duplicate objects. Copied from 3dsmax-mcp's max_client,
    which encodes exactly this as OUTCOME_UNKNOWN_ADVICE.
    """

    code = "busy"
    retryable = False


class MaxRestarted(MaxError):
    """Max stopped polling mid-request: it exited, crashed, or the bridge stopped."""

    code = "restarted"
    retryable = False


# Compat alias -- older call sites catch MaxTimeout.
MaxTimeout = MaxBusy


class _Task:
    __slots__ = ("id", "script", "event", "ok", "data", "error", "cancelled")

    def __init__(self, script: str) -> None:
        self.id = uuid.uuid4().hex
        self.script = script
        self.event = threading.Event()
        self.ok = False
        self.data = None
        self.error = None
        self.cancelled = False


class Bridge:
    """Owns the task queue and the loopback HTTP endpoint Max polls."""

    def __init__(self, host: str = "127.0.0.1", port: int = 47100) -> None:
        self.host = host
        self.port = port

        self._pending: "queue.Queue[_Task]" = queue.Queue()
        self._inflight: dict[str, _Task] = {}
        self._lock = threading.Lock()

        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

        self._last_poll = 0.0
        self._last_error: str | None = None
        self._served = 0

    # ------------------------------------------------------------------ life

    def start(self) -> None:
        if self._httpd is not None:
            return
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # keep-alive off: trivial framing
            server_version = "max-mcp/bridge"

            def log_message(self, *_a) -> None:  # silence stderr spam
                pass

            def do_GET(self) -> None:  # noqa: N802
                if not self.path.startswith("/poll"):
                    self.send_error(404)
                    return
                bridge._last_poll = time.monotonic()
                task = bridge._dequeue()
                if task is None:
                    self.send_response(200)
                    self.send_header("X-Task-Id", "")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = task.script.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("X-Task-Id", task.id)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                bridge._served += 1

            def do_POST(self) -> None:  # noqa: N802
                if not self.path.startswith("/result"):
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                raw = self.rfile.read(length) if length else b""
                task_id = self.headers.get("X-Task-Id", "")
                bridge._complete(task_id, raw)
                payload = b'{"ack":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="max-mcp-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    # --------------------------------------------------------------- queueing

    def _dequeue(self) -> _Task | None:
        while True:
            try:
                task = self._pending.get_nowait()
            except queue.Empty:
                return None
            if task.cancelled:
                continue
            with self._lock:
                self._inflight[task.id] = task
            return task

    def _complete(self, task_id: str, raw: bytes) -> None:
        with self._lock:
            task = self._inflight.pop(task_id, None)
        if task is None:
            return
        # utf-8-sig: tolerate a BOM if the sender emits one anyway
        text = raw.decode("utf-8-sig", "replace").strip()
        if not text:
            task.ok = False
            task.error = "empty response from 3ds Max"
            task.event.set()
            return
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # Max encoded something we cannot read: surface it verbatim.
            task.ok = False
            task.error = f"unparsable result from 3ds Max: {text[:2000]}"
            task.event.set()
            return
        if isinstance(payload, dict) and "ok" in payload:
            task.ok = bool(payload.get("ok"))
            task.data = payload.get("data")
            task.error = payload.get("error")
        else:
            task.ok = True
            task.data = payload
        task.event.set()

    # -------------------------------------------------------------------- api

    def call(self, script: str, timeout: float = 30.0) -> object:
        """Push a MAXScript snippet to Max and block until it answers."""
        self.start()

        # Max polls every 100 ms; give a cold start a moment before giving up,
        # otherwise the very first tool call always fails.
        if self._last_poll <= 0:
            deadline = time.monotonic() + min(3.0, timeout)
            while time.monotonic() < deadline and self._last_poll <= 0:
                time.sleep(0.05)
            if self._last_poll <= 0:
                raise MaxNotConnected(
                    "3ds Max never contacted the bridge on "
                    f"http://{self.host}:{self.port}. Load "
                    "maxscript/max_mcp_bridge.ms inside Max (Scripting > Run Script) "
                    "and check the MAXScript Listener for the startup line."
                )

        task = _Task(script)
        self._pending.put(task)
        if not task.event.wait(timeout):
            task.cancelled = True
            with self._lock:
                self._inflight.pop(task.id, None)

            # Distinguish "busy" from "gone": if Max is still polling right now,
            # it is alive and simply has not finished (or is blocked by a dialog).
            age = self.last_poll_age
            if age is not None and age > 3.0:
                raise MaxRestarted(
                    f"3ds Max stopped polling {age:.0f}s ago, so it exited, crashed, "
                    "or the bridge was stopped while this request was in flight. "
                    "The script may have partially executed -- inspect the scene "
                    "before re-running it. Nothing was retried automatically."
                )
            raise MaxBusy(
                f"3ds Max accepted the request but returned no result within "
                f"{timeout:.0f}s. It is still polling, so it is alive and most "
                "likely executing a long script or blocked by a modal dialog. "
                "THE SCRIPT MAY ALREADY HAVE CHANGED THE SCENE -- inspect before "
                "retrying, and press ESC in Max to abort the run."
            )
        if not task.ok:
            raise MaxError(task.error or "unknown MAXScript error")
        return task.data

    # ----------------------------------------------------------------- status

    @property
    def last_poll_age(self) -> float | None:
        if self._last_poll <= 0:
            return None
        return time.monotonic() - self._last_poll

    @property
    def online(self) -> bool:
        age = self.last_poll_age
        return age is not None and age < 3.0

    def stats(self) -> dict:
        return {
            "endpoint": f"http://{self.host}:{self.port}",
            "online": self.online,
            "last_poll_age_s": None if self.last_poll_age is None else round(self.last_poll_age, 2),
            "tasks_served": self._served,
            "inflight": len(self._inflight),
            "queued": self._pending.qsize(),
        }
