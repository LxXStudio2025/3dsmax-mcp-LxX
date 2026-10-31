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
import math
import os
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

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
        suffix = f"[max_error code={self.code} retryable={str(self.retryable).lower()}]"
        # A no-arg construction would otherwise leave a dangling blank line.
        return f"{text}\n{suffix}" if text else suffix


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

# Post-timeout wait for an outbox re-delivery: 1 result per tick, outbox max 16,
# idle tick 2 s.
_LATE_GRACE_S = 5.0


class _BridgeHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR lets competing servers bind the same live port.
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


_MAX_RESULT_BYTES = 32 * 1024 * 1024


class _Task:
    __slots__ = (
        "id",
        "script",
        "event",
        "ok",
        "data",
        "error",
        "cancelled",
        "completed",
    )

    def __init__(self, script: str) -> None:
        self.id = uuid.uuid4().hex
        self.script = script
        self.event = threading.Event()
        self.ok = False
        self.data = None
        self.error = None
        self.cancelled = False
        # Set under _lock the moment a result arrives, before the payload is
        # even parsed. Lets a timing-out caller tell "Max answered, still
        # decoding" apart from "Max never answered".
        self.completed = False


class Bridge:
    """Owns the task queue and the loopback HTTP endpoint Max polls."""

    def __init__(self, host: str = "127.0.0.1", port: int = 47100) -> None:
        self.host = host
        self.port = port

        self._pending: dict[str, _Task] = {}
        self._inflight: dict[str, _Task] = {}
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._generation = 0

        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

        self._last_poll = 0.0
        self._last_error: str | None = None
        self._served = 0

    # ------------------------------------------------------------------ life

    def start(self) -> None:
        with self._lifecycle_lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._httpd is not None:
            return
        # Windows SO_REUSEADDR lets a second bind attach to a live listener
        # (Unix only covers TIME_WAIT); two servers would split Max's poll
        # traffic silently.
        if self._port_in_use():
            raise MaxError(
                f"port {self.port} is already in use by another process -- most "
                "likely a running max_mcp server (the MCP connector), or another "
                "maxctl. Inside an MCP client just call the tools; from a shell "
                "stop the connector first or pass --port <free port>."
            )
        bridge = self
        generation = self._generation

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # keep-alive off: trivial framing
            server_version = "max-mcp/bridge"

            def log_message(self, *_a) -> None:  # silence stderr spam
                pass

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(10.0)

            def do_GET(self) -> None:  # noqa: N802
                if urlsplit(self.path).path != "/poll":
                    self.send_error(404)
                    return
                with bridge._lock:
                    if generation != bridge._generation:
                        self.send_error(503, "bridge generation ended")
                        return
                    bridge._last_poll = time.monotonic()
                task = bridge._dequeue(generation)
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
                with bridge._lock:
                    bridge._served += 1

            def do_POST(self) -> None:  # noqa: N802
                if urlsplit(self.path).path != "/result":
                    self.send_error(404)
                    return
                header = self.headers.get("Content-Length")
                if header is None:
                    self.send_error(411)
                    return
                try:
                    length = int(header)
                except ValueError:
                    self.send_error(400, "invalid Content-Length")
                    return
                if length < 0 or length > _MAX_RESULT_BYTES:
                    self.send_error(413 if length > _MAX_RESULT_BYTES else 400)
                    return
                try:
                    raw = self.rfile.read(length) if length else b""
                except (OSError, TimeoutError):
                    self.send_error(408)
                    return
                if len(raw) != length:
                    self.send_error(400, "incomplete result body")
                    return
                task_id = self.headers.get("X-Task-Id", "")
                bridge._complete(task_id, raw)
                payload = b'{"ack":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._httpd = _BridgeHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="max-mcp-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            # End the generation and wake callers before shutdown's polling
            # delay. Old handlers can no longer take work during that delay.
            with self._lock:
                tasks = list(self._inflight.values())
                self._inflight.clear()
                tasks.extend(self._pending.values())
                self._pending.clear()
                self._generation += 1
                self._last_poll = 0.0
                for task in tasks:
                    task.cancelled = True
                    task.ok = False
                    task.error = MaxRestarted(
                        "bridge stopped while a request was pending; a task already "
                        "taken by Max may still run. Inspect the scene before retrying."
                    )
                    task.event.set()
            if self._httpd is not None:
                self._httpd.shutdown()
                self._httpd.server_close()
                self._httpd = None
            if self._thread is not None:
                self._thread.join(timeout=2.0)
                self._thread = None

    def _port_in_use(self) -> bool:
        """True if something is already accepting connections on our port."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            return probe.connect_ex((self.host, self.port)) == 0

    # --------------------------------------------------------------- queueing

    def _dequeue(self, generation: int | None = None) -> _Task | None:
        with self._lock:
            if generation is not None and generation != self._generation:
                return None
            while self._pending:
                task_id = next(iter(self._pending))
                task = self._pending.pop(task_id)
                if task.cancelled:
                    continue
                self._inflight[task.id] = task
                return task
            return None

    @staticmethod
    def _decode_result(raw: bytes) -> tuple[bool, object, str | None]:
        """Decode outside the state lock; publication is a separate atomic step."""
        try:
            text = raw.decode("utf-8-sig").strip()
            if not text:
                return False, None, "empty response from 3ds Max"
            payload = json.loads(text)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            return False, None, f"invalid result from 3ds Max: {type(exc).__name__}"
        pending = [(payload, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 64:
                return (
                    False,
                    None,
                    "invalid result from 3ds Max: nesting exceeds 64 levels",
                )
            if isinstance(value, float) and not math.isfinite(value):
                return False, None, "invalid result from 3ds Max: non-finite number"
            if isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        if isinstance(payload, dict) and "ok" in payload:
            if not isinstance(payload["ok"], bool):
                return False, None, "invalid result envelope: ok must be a boolean"
            error = payload.get("error")
            if error is not None and not isinstance(error, str):
                return (
                    False,
                    None,
                    "invalid result envelope: error must be a string or null",
                )
            return payload["ok"], payload.get("data"), error
        return True, payload, None

    def _complete(self, task_id: str, raw: bytes) -> None:
        with self._lock:
            task = self._inflight.get(task_id)
        if task is None:
            return
        ok, data, error = self._decode_result(raw)
        with self._lock:
            # stop(), timeout, or another result may have finalized the task
            # while decoding. Never resurrect it or overwrite its verdict.
            if self._inflight.get(task_id) is not task or task.cancelled:
                return
            self._inflight.pop(task_id)
            task.ok, task.data, task.error = ok, data, error
            task.completed = True
            task.event.set()

    def call(self, script: str, timeout: float = 30.0) -> object:
        """Push a MAXScript snippet to Max and block until it answers."""
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a finite positive number")
        with self._lifecycle_lock:
            self.start()
            generation = self._generation

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
        with self._lifecycle_lock:
            if self._generation != generation or self._httpd is None:
                raise MaxRestarted("bridge stopped before the task could be queued")
            with self._lock:
                self._pending[task.id] = task
        if not task.event.wait(timeout):
            # POST may fail after Max already ran the script; the outbox
            # re-delivers on the next tick. Without this wait the task is popped
            # and the result discarded.
            if not task.event.wait(_LATE_GRACE_S):
                with self._lock:
                    # Finalization competes with result publication and stop
                    # under one lock, not a check-then-act event race.
                    timed_out = not task.event.is_set()
                    if timed_out:
                        task.cancelled = True
                        self._pending.pop(task.id, None)
                        was_taken = self._inflight.pop(task.id, None) is not None
                if timed_out:
                    self._raise_no_result(timeout, was_taken)
        if not task.ok:
            if isinstance(task.error, MaxError):
                raise task.error
            raise MaxError(task.error or "unknown MAXScript error")
        return task.data

    def _raise_no_result(self, timeout: float, was_taken: bool) -> None:
        """Raise the right error for a task that never answered.

        `was_taken` is the discriminator, not last_poll_age: Max cannot poll
        while it executes a script (single UI thread), so a busy Max looks
        exactly like a dead one from the outside. Whether Max picked the task
        up, however, is unambiguous -- it is in _inflight only after a poll.
        """
        if not was_taken:
            age = self.last_poll_age
            when = (
                "has never polled this bridge"
                if age is None
                else f"last polled {age:.0f}s ago"
            )
            raise MaxRestarted(
                f"3ds Max never picked this task up ({when}), so it exited, crashed, "
                "or the bridge was stopped before it could run. "
                "Nothing was executed -- but nothing was retried either, so check "
                "the bridge state before sending it again."
            )
        raise MaxBusy(
            f"3ds Max accepted this task but produced no result within "
            f"{timeout:.0f}s (+{_LATE_GRACE_S:.0f}s grace for a re-delivered result). "
            "It had already picked the task up, so the script IS running, finished "
            "without reporting, or is blocked by a modal dialog -- Max cannot poll "
            "while it executes, which is why there is no further signal from here. "
            "THE SCRIPT MAY ALREADY HAVE CHANGED THE SCENE -- inspect before "
            "retrying, and press ESC in Max to abort the run."
        )

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
        with self._lock:
            age = None if self._last_poll <= 0 else time.monotonic() - self._last_poll
            return {
                "endpoint": f"http://{self.host}:{self.port}",
                "online": age is not None and age < 3.0,
                "last_poll_age_s": None if age is None else round(age, 2),
                "tasks_served": self._served,
                "inflight": len(self._inflight),
                "queued": len(self._pending),
            }
