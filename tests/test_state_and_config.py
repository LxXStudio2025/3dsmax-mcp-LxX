"""State and configuration regressions from the second source review."""

import concurrent.futures
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import configure_codex

from max_mcp.bridge import Bridge, MaxBusy, MaxRestarted


class ResultPublicationTests(unittest.TestCase):
    def run_decoding_race(self, stop=False):
        bridge = Bridge(port=0)
        bridge.start()
        bridge._last_poll = time.monotonic()
        self.addCleanup(bridge.stop)
        decoding = threading.Event()
        release = threading.Event()
        original = bridge._decode_result

        def delayed(raw):
            decoding.set()
            if not release.wait(3):
                raise RuntimeError("test decoder release timed out")
            return original(raw)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            with (
                mock.patch.object(bridge, "_decode_result", side_effect=delayed),
                mock.patch("max_mcp.bridge._LATE_GRACE_S", 0.01),
            ):
                caller = pool.submit(bridge.call, "result race", 0.2)
                deadline = time.monotonic() + 1
                task = None
                while task is None and time.monotonic() < deadline:
                    task = bridge._dequeue()
                    if task is None:
                        time.sleep(0.001)
                self.assertIsNotNone(task)
                decoder = pool.submit(
                    bridge._complete, task.id, b'{"ok":true,"data":7}'
                )
                self.assertTrue(decoding.wait(1))
                try:
                    if stop:
                        bridge.stop()
                    with self.assertRaises(MaxRestarted if stop else MaxBusy):
                        caller.result(timeout=1)
                finally:
                    release.set()
                decoder.result(timeout=1)
        self.assertFalse(task.ok)
        self.assertNotIn(task.id, bridge._inflight)

    def test_stop_wakes_caller_during_decode(self):
        self.run_decoding_race(stop=True)

    def test_late_decoding_cannot_overwrite_timeout(self):
        self.run_decoding_race()

    def test_expired_queued_tasks_do_not_accumulate(self):
        bridge = Bridge(port=0)
        bridge.start()
        self.addCleanup(bridge.stop)
        bridge._last_poll = time.monotonic()
        with mock.patch("max_mcp.bridge._LATE_GRACE_S", 0):
            for _ in range(8):
                with self.assertRaises(MaxRestarted):
                    bridge.call("not picked up", 0.001)
        self.assertEqual(bridge.stats()["queued"], 0)
        self.assertIsNone(bridge._dequeue())

    def test_malformed_payloads_return_explicit_failure(self):
        for raw in [
            b"",
            b"\xff",
            b"not-json",
            b'{"ok":"false"}',
            b"[" * 2000 + b"]" * 2000,
        ]:
            with self.subTest(raw=raw[:20]):
                ok, _, error = Bridge._decode_result(raw)
                self.assertFalse(ok)
                self.assertTrue(error)

    def test_obsolete_http_generation_cannot_take_new_work(self):
        bridge = Bridge(port=0)
        bridge.start()
        self.addCleanup(bridge.stop)
        generation = bridge._generation
        bridge.stop()
        bridge.start()
        bridge._last_poll = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            caller = pool.submit(bridge.call, "new generation", 2)
            deadline = time.monotonic() + 1
            while not bridge._pending and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertIsNone(bridge._dequeue(generation))
            task = bridge._dequeue(bridge._generation)
            self.assertIsNotNone(task)
            bridge._complete(task.id, b'{"ok":true,"data":42}')
            self.assertEqual(caller.result(timeout=1), 42)


class ReflectionCleanupTests(unittest.TestCase):
    def test_failed_reflection_removes_temporary_output(self):
        from max_mcp import tools

        with tempfile.TemporaryDirectory() as directory:

            def failed(script, **kwargs):
                for token in script.split('"'):
                    if token.endswith(".txt"):
                        Path(token.replace("\\\\", "\\")).write_text(
                            "partial", encoding="utf-8"
                        )
                        break
                return False

            with (
                mock.patch.object(tools, "SHOT_DIR", directory),
                mock.patch.object(tools, "call", side_effect=failed),
            ):
                with self.assertRaises(tools.MaxError):
                    tools.max_api("MissingClass")
            self.assertEqual(list(Path(directory).iterdir()), [])


class ConfigDocumentTests(unittest.TestCase):
    def test_multiline_header_text_and_comments_survive(self):
        original = '''# top comment
instructions = """
[mcp_servers.3dsmax2016]
Keep this literal instruction
"""
[mcp_servers.3dsmax2016] # server comment
command = "python"
args = ["-m", "max_mcp.server"]
enabled = false # user preference
[projects."C:/repo"]
trust_level = "trusted"
'''
        updated = configure_codex.patch_config(original, Path("D:/new"), ["2016"])
        parsed = configure_codex.tomllib.loads(updated)
        self.assertIn("Keep this literal instruction", parsed["instructions"])
        for comment in ["# top comment", "# server comment", "# user preference"]:
            self.assertIn(comment, updated)
        self.assertFalse(parsed["mcp_servers"]["3dsmax2016"]["enabled"])
        self.assertEqual(
            updated, configure_codex.patch_config(updated, Path("D:/new"), ["2016"])
        )

    def test_nested_and_inline_user_settings_survive(self):
        original = """[mcp_servers.3dsmax2016]
command="python"
args=["-m","max_mcp.server"]
env={CUSTOM="kept"}
custom=[{name="A", flags=[true,false]}]
[mcp_servers.3dsmax2016.experimental.nested]
answer=42
"""
        updated = configure_codex.patch_config(
            original, Path("D:/new"), ["2016", "2016"]
        )
        parsed = configure_codex.tomllib.loads(updated)["mcp_servers"]["3dsmax2016"]
        self.assertEqual(parsed["experimental"]["nested"]["answer"], 42)
        self.assertEqual(parsed["custom"], [{"name": "A", "flags": [True, False]}])
        self.assertEqual(parsed["env"]["CUSTOM"], "kept")


if __name__ == "__main__":
    unittest.main()
