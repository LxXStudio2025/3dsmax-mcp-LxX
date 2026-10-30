import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import configure_codex  # noqa: E402
import install  # noqa: E402


class PortabilityTests(unittest.TestCase):
    def test_relocation_updates_only_managed_entries(self):
        original = "\n".join(
            [
                'title = "keep"',
                "[mcp_servers.other]",
                'command = "leave-me"',
                '[mcp_servers."3dsmax2016"]',
                'command = "C:/old/python.exe"',
                'args = ["-m", "max_mcp.server"]',
                "enabled = false",
                "tool_timeout_sec = 900",
                '[mcp_servers."3dsmax2016".env]',
                'CUSTOM = "keep"',
                'MAXMCP_PORT = "47100"',
                '[projects."C:/old"]',
                'trust_level = "trusted"',
                "",
            ]
        )
        new = configure_codex.patch_config(original, Path("D:/moved repo"), ["2016"])
        data = configure_codex.tomllib.loads(new)
        entry = data["mcp_servers"]["3dsmax2016"]
        self.assertFalse(entry["enabled"])
        self.assertEqual(entry["tool_timeout_sec"], 900)
        self.assertEqual(entry["env"]["CUSTOM"], "keep")
        self.assertIn("moved repo", entry["command"])
        self.assertEqual(entry["cwd"], str(Path("D:/moved repo")))
        self.assertEqual(data["mcp_servers"]["other"]["command"], "leave-me")
        self.assertEqual(data["projects"]["C:/old"]["trust_level"], "trusted")
        self.assertEqual(
            new, configure_codex.patch_config(new, Path("D:/moved repo"), ["2016"])
        )

    def test_unrelated_same_name_is_rejected(self):
        with self.assertRaises(ValueError):
            configure_codex.patch_config(
                '[mcp_servers.3dsmax2016]\ncommand="unrelated"\n', ROOT, ["2016"]
            )

    def test_stale_registry_does_not_mask_new_location(self):
        with (
            mock.patch.object(
                install,
                "_subkeys",
                side_effect=lambda p: ["2016"] if p == install.REG_BASE else [],
            ),
            mock.patch.object(install, "_read", return_value="C:/gone"),
            mock.patch.object(install.os.path, "isfile", return_value=False),
            mock.patch.object(
                install, "_from_filesystem", return_value={"2016": "D:/moved Max"}
            ),
        ):
            self.assertEqual(
                install.discover(), [{"version": "2016", "install_dir": "D:/moved Max"}]
            )

    def test_launcher_is_relative_and_failed_load_can_retry(self):
        text = install.LAUNCHER_TEMPLATE
        self.assertIn("getSourceFileName()", text)
        self.assertNotIn("{main_script}", text)
        self.assertNotIn("G:", text)
        self.assertLess(
            text.index("fileIn payload"), text.index("__MAXMCP_LAUNCHED = true")
        )

    def test_checked_in_launcher_matches_installer(self):
        self.assertEqual(
            (ROOT / "maxscript/start_maxmcp.ms").read_text(encoding="utf-8"),
            install.LAUNCHER_TEMPLATE,
        )

    def test_install_creates_self_contained_relocatable_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Max"
            (root / "scripts/startup").mkdir(parents=True)
            self.assertEqual(
                install.install_one(
                    {"version": "2016", "install_dir": str(root)}, ["en"], quiet=True
                ),
                0,
            )
            self.assertTrue((root / "scripts/maxmcp/max_mcp_bridge.ms").is_file())
            launcher = (root / "scripts/startup/start_maxmcp.ms").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(str(root), launcher)
            moved = Path(tmp) / "Moved"
            root.rename(moved)
            self.assertTrue(
                (moved / "scripts/startup/../maxmcp/max_mcp_bridge.ms").is_file()
            )


if __name__ == "__main__":
    unittest.main()
