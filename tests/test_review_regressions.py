"""Offline regressions for the 2026-10-30 source review. No live Max needed."""

import concurrent.futures
import http.client
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from max_mcp import tools
from max_mcp.bridge import _MAX_RESULT_BYTES, Bridge, MaxRestarted, _Task


class BridgeLifecycleTests(unittest.TestCase):
    def make_bridge(self):
        b = Bridge(port=0)
        b.start()
        self.addCleanup(b.stop)
        return b

    def test_stop_discards_queued_tasks_and_wakes_callers(self):
        b = self.make_bridge()
        b._last_poll = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(b.call, "old task must never execute", 5)
            deadline = time.monotonic() + 2
            while not b._pending and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(b._pending)
            b.stop()
            with self.assertRaises(MaxRestarted):
                future.result(timeout=1)
        b.start()
        self.assertIsNone(b._dequeue())
        self.assertIsNone(b.last_poll_age)

    def test_stop_wakes_inflight_caller_and_ignores_late_result(self):
        b = self.make_bridge()
        b._last_poll = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(b.call, "taken task", 5)
            deadline = time.monotonic() + 2
            while not b._pending and time.monotonic() < deadline:
                time.sleep(0.005)
            task = b._dequeue()
            self.assertIsNotNone(task)
            b.stop()
            with self.assertRaises(MaxRestarted):
                future.result(timeout=1)
        b._complete(task.id, b'{"ok":true,"data":1}')
        self.assertFalse(task.ok)
        self.assertEqual(b._inflight, {})

    def test_simultaneous_start_creates_one_listener(self):
        b = Bridge(port=0)
        self.addCleanup(b.stop)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: b.start(), range(16)))
        self.assertTrue(b._thread.is_alive())
        self.assertIsNotNone(b._httpd)

    def test_nonfinite_timeouts_rejected_before_start(self):
        b = Bridge(port=0)
        for value in [0, -1, float("nan"), float("inf"), True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                b.call("x", value)
        self.assertIsNone(b._httpd)

    def test_false_string_is_not_success(self):
        b = Bridge(port=0)
        task = _Task("x")
        b._inflight[task.id] = task
        b._complete(task.id, b'{"ok":"false","data":123}')
        self.assertTrue(task.event.is_set())
        self.assertFalse(task.ok)

    def test_http_length_and_exact_routes(self):
        b = self.make_bridge()
        port = b._httpd.server_port
        for method, path, headers, expected in [
            ("GET", "/pollution", {}, 404),
            ("POST", "/result_extra", {"Content-Length": "0"}, 404),
            ("POST", "/result", {"Content-Length": "-1"}, 400),
            ("POST", "/result", {"Content-Length": "oops"}, 400),
            ("POST", "/result", {"Content-Length": str(_MAX_RESULT_BYTES + 1)}, 413),
        ]:
            with self.subTest(path=path, headers=headers):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                try:
                    conn.request(method, path, headers=headers)
                    resp = conn.getresponse()
                    resp.read()
                    self.assertEqual(resp.status, expected)
                finally:
                    conn.close()
        self.assertEqual(b._inflight, {})


class ToolBoundaryTests(unittest.TestCase):
    def test_invalid_creation_never_dispatches(self):
        with mock.patch.object(tools, "call") as call:
            for kwargs in [
                {"pos": []},
                {"pos": [1, 2]},
                {"pos": [1, 2, 3, 4]},
                {"pos": [0, 0, float("inf")]},
                {"params": {"w": float("nan")}},
                {"params": {"pos": 3}},
            ]:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    tools.max_create_object("box", **kwargs)
            call.assert_not_called()

    def test_nonfinite_vectors_rejected(self):
        for value in [[1, 2, float("nan")], float("inf")]:
            with self.assertRaises(ValueError):
                tools.mxs_lit(value)

    def test_creation_has_undo_block(self):
        with mock.patch.object(tools, "call", return_value=["created", "test"]) as call:
            tools.max_create_object("box", pos=[1, 2, 3], params={"w": 2})
        self.assertIn('undo "MCP: create object" on', call.call_args.args[0])

    def test_file_operations_use_validated_absolute_path(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            root = Path(directory)
            source = root / "scene.fbx"
            source.touch()
            for action, path in [
                ("import", source),
                ("merge", source),
                ("export", root / "new.fbx"),
            ]:
                with mock.patch.object(
                    tools, "call", return_value=["ok", True]
                ) as call:
                    tools.max_file_io(action, str(path))
                self.assertIn(
                    tools.mxs_str(str(path.resolve())), call.call_args.args[0]
                )
        with mock.patch.object(tools, "call") as call, self.assertRaises(ValueError):
            tools.max_file_io("export", "")

    def test_file_plugin_false_result_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scene.fbx"
            source.touch()
            for action, path in [
                ("import", source),
                ("merge", source),
                ("export", Path(directory) / "out.fbx"),
            ]:
                with mock.patch.object(
                    tools, "call", return_value=["ok", True]
                ) as call:
                    tools.max_file_io(action, str(path))
                self.assertIn("if __success != true do throw", call.call_args.args[0])

    def test_import_primitive_uses_shared_file_checks(self):
        with mock.patch.object(
            tools, "max_file_io", return_value={"added": 2, "path": "C:/asset.fbx"}
        ) as io:
            self.assertEqual(
                tools.max_create_object("import", params={"path": "relative.fbx"}),
                {"imported": 2, "path": "C:/asset.fbx"},
            )
            io.assert_called_once_with("import", "relative.fbx")

    def test_reflection_pattern_is_escaped(self):
        pattern = 'x"; throw "should not execute'

        def fake(script, **kwargs):
            self.assertIn(
                "showClass " + tools.mxs_str("Box." + pattern) + " to:__f", script
            )
            return False

        with (
            mock.patch.object(tools, "call", side_effect=fake),
            self.assertRaises(tools.MaxError),
        ):
            tools.max_api("Box", pattern)

    def test_render_size_rejected_before_dispatch(self):
        with mock.patch.object(tools, "call") as call:
            for size in [0, -1, 2.5, True]:
                with self.assertRaises(ValueError):
                    tools.capture_to_file(render=True, width=size)
            call.assert_not_called()


class CliTests(unittest.TestCase):
    def test_file_overwrite_flag_is_forwarded(self):
        from max_mcp import cli

        args = cli.build_parser().parse_args(
            ["file", "export", "scene.fbx", "--overwrite"]
        )
        with (
            mock.patch.object(tools, "max_file_io", return_value={"ok": True}) as io,
            mock.patch.object(cli, "_emit"),
        ):
            self.assertEqual(cli.cmd_file(args), 0)
        io.assert_called_once_with("export", "scene.fbx", False, True)

    def test_main_closes_bridge_on_error(self):
        from max_mcp import cli

        with (
            mock.patch.object(tools, "max_status", side_effect=ValueError("test")),
            mock.patch.object(tools.BRIDGE, "stop") as stop,
        ):
            self.assertEqual(cli.main(["status"]), 1)
        stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
