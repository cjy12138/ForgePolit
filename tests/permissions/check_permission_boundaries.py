import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.permissions.boundaries import check_path_boundary
from forgepilot.permissions.models import ToolPermissionRequest


class PathBoundaryChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(
            prefix="forgepilot-permission-check-"
        )
        self.addCleanup(temporary.cleanup)

        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

        (self.workspace / "main.py").write_text(
            "print('hello')\n",
            encoding="utf-8",
        )

        self.outside = self.root / "outside.txt"
        self.outside.write_text(
            "outside workspace\n",
            encoding="utf-8",
        )

    def check(self, path, *, mode="default", tool="read_file"):
        return check_path_boundary(
            ToolPermissionRequest(
                tool_name=tool,
                arguments={"path": path},
                workspace=str(self.workspace),
                mode=mode,
            )
        )

    def create_link(self, name, target):
        link = self.workspace / name

        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(
                f"当前系统无法创建符号链接：{exc}"
            )

        return link

    def test_normal_paths_continue(self):
        for path in ("main.py", "./main.py", "."):
            with self.subTest(path=path):
                result = self.check(path)

                # 通过边界检查，不代表最终允许执行。
                self.assertEqual(result.action, "abstain")

    def test_parent_and_absolute_paths_are_denied(self):
        for path in (
            "../outside.txt",
            str(self.outside),
            r"C:\outside.txt",
            r"C:outside.txt",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    self.check(path).action,
                    "deny",
                )

    def test_protected_paths_are_denied_in_every_mode(self):
        modes = (
            "default",
            "accept_edits",
            "plan",
            "bypass",
            "auto",
            "bubble",
        )

        paths = (
            ".git/config",
            ".forgepilot/settings.json",
            ".claude/settings.json",
            ".codex/config.toml",
            ".agents/settings.json",
            ".env",
            ".env.production",
        )

        for mode in modes:
            for path in paths:
                with self.subTest(mode=mode, path=path):
                    self.assertEqual(
                        self.check(path, mode=mode).action,
                        "deny",
                    )

    def test_ambiguous_names_are_denied(self):
        paths = [
            "main.py:secret",
            "main.py.",
            "main.py ",
            "bad\x00name",
        ]

        if os.name == "nt":
            paths.extend(["CON", "NUL.txt", "COM1"])

        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(
                    self.check(path).action,
                    "deny",
                )

    def test_link_to_outside_is_denied(self):
        self.create_link("outside_link.txt", self.outside)

        result = self.check("outside_link.txt")

        self.assertEqual(result.action, "deny")
        self.assertIn(
            "不在允许的工作区内",
            result.reason,
        )

    def test_link_to_protected_file_is_denied(self):
        protected = self.workspace / ".git"
        protected.mkdir()

        target = protected / "config"
        target.write_text("test configuration\n", encoding="utf-8")

        self.create_link("config_link.txt", target)

        result = self.check("config_link.txt")

        self.assertEqual(result.action, "deny")
        self.assertIn("受保护位置", result.reason)

    def test_link_inside_workspace_continues(self):
        self.create_link(
            "main_link.py",
            self.workspace / "main.py",
        )

        self.assertEqual(
            self.check("main_link.py").action,
            "abstain",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
