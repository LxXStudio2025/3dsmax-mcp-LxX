"""Regression coverage for defects reproduced in real Max 2016 validation."""

import asyncio
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from max_mcp import tools
from max_mcp.bridge import Bridge


class ProtocolValueTests(unittest.TestCase):
    def test_nonfinite_json_values_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
            with self.subTest(value=value):
                ok, _, error = Bridge._decode_result(
                    ('{"ok":true,"data":{"nested":[' + value + "]}}").encode()
                )
                self.assertFalse(ok)
                self.assertIn("non-finite", error)

    def test_error_payload_requires_string_or_null(self):
        for error in ("{}", "[]", "12", "false"):
            with self.subTest(error=error):
                ok, _, message = Bridge._decode_result(
                    ('{"ok":false,"error":' + error + "}").encode()
                )
                self.assertFalse(ok)
                self.assertIsInstance(message, str)
                self.assertIn("error must be", message)

    def test_valid_payload_remains_compatible(self):
        self.assertEqual(
            Bridge._decode_result(b'{"ok":true,"data":[1,2.5,null,true]}'),
            (True, [1, 2.5, None, True], None),
        )
        self.assertEqual(
            Bridge._decode_result(b'{"ok":false,"error":"plugin failed"}'),
            (False, None, "plugin failed"),
        )


class StatusVersionTests(unittest.TestCase):
    def test_server_version_identifies_loaded_python_source(self):
        from max_mcp import __version__

        with mock.patch.object(
            tools,
            "call",
            return_value=["max_version", "2016", "bridge_version", "0.3.8"],
        ):
            result = tools.max_status()
        self.assertEqual(result["server_version"], __version__)
        self.assertEqual(result["bridge_version"], "0.3.8")


class MergeArgumentTests(unittest.TestCase):
    def test_merge_uses_max_2016_supported_noninteractive_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Path(folder) / "fixture.max"
            fixture.touch()
            with mock.patch.object(tools, "call", return_value=["ok", True]) as call:
                tools.max_file_io("merge", str(fixture))
            script = call.call_args.args[0]
            self.assertNotIn("#noPrompt", script)
            for flag in (
                "#autoRenameDups",
                "#renameMtlDups",
                "#neverReparent",
                "quiet:true",
            ):
                self.assertIn(flag, script)
            self.assertIn(tools.mxs_str(str(fixture.resolve())), script)


class LiveHarnessTests(unittest.TestCase):
    def test_active_connector_is_rejected_before_server_start(self):
        import stdio_live

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                stdio_live.ensure_idle_port(listener.getsockname()[1])

    def test_roundtrip_requires_specific_instance(self):
        import stdio_live

        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            stdio_live.parse_args(["--roundtrip", "--port", "47123"])

    def test_status_identity_checks_version_pid_and_port(self):
        import stdio_live

        status = {"max_version": "2016", "pid": 123, "port": 47123}
        stdio_live.assert_identity(status, "2016", 123, 47123)
        with self.assertRaises(RuntimeError):
            stdio_live.assert_identity(status, "2016", 456, 47123)
        with self.assertRaises(RuntimeError):
            stdio_live.assert_identity(status, "2025", 123, 47123)

    def test_response_decoder_supports_sdk_aliases_and_errors(self):
        import stdio_live

        for key in ("structuredContent", "structured_content"):
            raw = SimpleNamespace(
                model_dump=lambda k=key: {k: {"objects": 0}, "content": []}
            )
            self.assertEqual(stdio_live.decode_result(raw), {"objects": 0})
        raw = SimpleNamespace(
            model_dump=lambda: {
                "is_error": True,
                "content": [{"type": "text", "text": "failure"}],
            }
        )
        with self.assertRaisesRegex(RuntimeError, "failure"):
            stdio_live.decode_result(raw)


class StartupWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_not_connected_is_retried(self):
        import stdio_live

        status = {"pid": 123, "objects": 0}
        call = mock.AsyncMock(
            side_effect=[
                RuntimeError("offline [max_error code=not_connected retryable=true]"),
                status,
            ]
        )
        with mock.patch.object(
            stdio_live.asyncio, "sleep", new_callable=mock.AsyncMock
        ):
            self.assertEqual(await stdio_live.wait_for_status(call, 1), status)
        self.assertEqual(call.await_count, 2)

    async def test_busy_is_never_retried(self):
        import stdio_live

        call = mock.AsyncMock(
            side_effect=RuntimeError("busy [max_error code=busy retryable=false]")
        )
        with self.assertRaisesRegex(RuntimeError, "code=busy"):
            await stdio_live.wait_for_status(call, 1)
        self.assertEqual(call.await_count, 1)

    async def test_wait_is_bounded(self):
        import stdio_live

        async def blocked(*args):
            await asyncio.Event().wait()

        with self.assertRaisesRegex(RuntimeError, "startup timeout"):
            await stdio_live.wait_for_status(blocked, 0.01)

    def test_nested_error_summary_keeps_root_failure(self):
        import stdio_live

        outer = RuntimeError("task group")
        outer.exceptions = (ValueError("underlying error"),)
        self.assertEqual(
            stdio_live.error_summary(outer), "ValueError: underlying error"
        )


if __name__ == "__main__":
    unittest.main()
