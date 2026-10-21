"""Pico 工具层融合验证：只使用临时工作区，不调用模型 API。"""
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.environments.local import LocalEnvironment
from forgepilot.permissions.audit import PermissionAuditLog
from forgepilot.permissions.models import PermissionRule, ToolPermissionRequest
from forgepilot.permissions.rules import rule_matches
from forgepilot.skills.catalog import KNOWN_TOOL_NAMES
from forgepilot.skills.store import KNOWN_TOOL_NAMES as STORE_TOOL_NAMES
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.edit import build_edit_tools
from forgepilot.tools.files import build_file_tools


class ToolGuardChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="forgepilot-tool-guards-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.context = ToolContext(self.root, task="test")
        self.registry = ToolRegistry(build_file_tools(), build_edit_tools())
        self.registry.configure_permissions(mode="accept_edits")

    def execute(self, name, **arguments):
        return self.registry.execute(name, arguments, self.context)

    def test_create_empty_nested_file_and_explicit_overwrite(self):
        self.assertTrue(self.execute("write_file", path="src/a.py", content="").ok)
        target = self.root / "src/a.py"
        self.assertEqual(target.read_bytes(), b"")
        self.assertFalse(self.execute("write_file", path="src/a.py", content="hello").ok)
        self.assertEqual(target.read_bytes(), b"")
        self.assertTrue(self.execute("write_file", path="src/a.py", content="你好\r\n", overwrite=True).ok)
        self.assertEqual(target.read_bytes(), "你好\r\n".encode())

    def test_failed_replace_keeps_original(self):
        target = self.root / "a.txt"
        target.write_text("before", encoding="utf-8")
        with patch("forgepilot.tools.edit.os.replace", side_effect=OSError("failed")):
            result = self.execute("write_file", path="a.txt", content="after", overwrite=True)
        self.assertFalse(result.ok)
        self.assertEqual(target.read_text(encoding="utf-8"), "before")
        self.assertEqual(list(self.root.glob(".forgepilot-write-*")), [])

    def test_write_boundaries_and_input_rejection_have_no_side_effects(self):
        for mode in ("bypass", "accept_edits"):
            self.registry.configure_permissions(mode=mode)
            for name in ("../escape.txt", str(self.root / "absolute.txt"), ".git/config", ".env", ".forgepilot/state"):
                self.assertFalse(self.execute("write_file", path=name, content="bad").ok, name)
        for args in (
            {"path": ".", "content": "bad"},
            {"path": "a", "content": "\x00"},
            {"path": "a", "content": "中" * 400_000},
            {"path": "a", "content": "a", "overwrite": "true"},
        ):
            self.assertFalse(self.execute("write_file", **args).ok)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_escape_is_denied(self):
        with TemporaryDirectory() as outside:
            link = self.root / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("此系统未授予创建符号链接权限")
            self.registry.configure_permissions(mode="bypass")
            self.assertFalse(self.execute("write_file", path="link/out.txt", content="bad").ok)
            self.assertFalse((Path(outside) / "out.txt").exists())

    def test_write_permissions_and_rule_path_matching(self):
        approval = Mock(return_value=False)
        self.registry.configure_permissions(mode="default", resolver=approval)
        self.assertFalse(self.execute("write_file", path="a", content="x").ok)
        self.assertEqual(approval.call_count, 1)
        self.registry.configure_permissions(mode="plan", resolver=approval)
        self.assertFalse(self.execute("write_file", path="a", content="x").ok)
        self.assertEqual(approval.call_count, 1)
        self.assertFalse((self.root / "a").exists())
        request = ToolPermissionRequest(tool_name="write_file", arguments={"path": "src/a.py"}, workspace=str(self.root))
        self.assertTrue(rule_matches(PermissionRule(action="deny", tool="write_file", pattern="src/*"), request))

    def test_read_lines_and_original_character_api(self):
        content = "first\r\n第二行\r\nthird\r\n"
        (self.root / "a").write_bytes(content.encode())
        result = self.execute("read_file", path="a", start_line=2, end_line=2)
        self.assertTrue(result.ok)
        self.assertEqual(result.output.split("\n\n", 1)[1], "2: 第二行\r\n")
        original = self.execute("read_file", path="a", offset=0, limit=200)
        self.assertEqual(original.output.split("\n\n", 1)[1], content)
        # 行模式受字符预算限制，续读偏移必须对应原文（含 CRLF）。
        clipped = self.execute("read_file", path="a", start_line=2, end_line=3, limit=2)
        self.assertIn("offset 9 (character mode)", clipped.output)
        rest = self.execute("read_file", path="a", offset=9)
        self.assertEqual(rest.output.split("\n\n", 1)[1], content[9:])

    def test_invalid_line_ranges(self):
        (self.root / "a").write_text("first\nsecond", encoding="utf-8")
        for args in ({"start_line": 0}, {"end_line": 2}, {"start_line": 2, "end_line": 1},
                     {"start_line": 1, "offset": 2}, {"start_line": 3}):
            self.assertFalse(self.execute("read_file", path="a", **args).ok)

    def test_repeat_blocked_before_approval_and_audited(self):
        calls = Mock(return_value="done")
        self.registry._tools["write_file"]["handler"] = calls
        self.registry.configure_permissions(mode="bypass")
        path = self.root / "audit.jsonl"
        self.registry.configure_audit(PermissionAuditLog(path, session_id="test"))
        self.assertTrue(self.execute("write_file", path="a", content="private").ok)
        self.assertTrue(self.execute("write_file", path="a", content="private", overwrite=False).ok)
        approval = Mock(return_value=True)
        self.registry.configure_permissions(mode="default", resolver=approval)
        result = self.execute("write_file", path="a", content="private")
        self.assertFalse(result.ok)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(approval.call_count, 0)
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[-2]["reason_code"], "repeated_tool_call")
        self.assertFalse(records[-1]["executed"])
        self.assertNotIn("private", path.read_text(encoding="utf-8"))

    def test_other_tool_and_new_turn_allow_repeat(self):
        calls = Mock(return_value="done")
        self.registry._tools["write_file"]["handler"] = calls
        for _ in range(2):
            self.assertTrue(self.execute("write_file", path="a", content="x").ok)
        self.assertFalse(self.execute("write_file", path="a", content="x").ok)
        self.execute("list_files")
        self.assertTrue(self.execute("write_file", path="a", content="x").ok)
        self.assertTrue(self.execute("write_file", path="a", content="x").ok)
        self.registry.begin_turn()
        self.assertTrue(self.execute("write_file", path="a", content="x").ok)

    def test_child_selection_has_independent_guard(self):
        for _ in range(2):
            self.assertTrue(self.execute("list_files").ok)
        child = self.registry.select_tools({"list_files"})
        self.assertTrue(child.execute("list_files", {}, self.context).ok)
        self.assertFalse(self.execute("list_files").ok)
        self.assertEqual(KNOWN_TOOL_NAMES, STORE_TOOL_NAMES)
        self.assertIn("write_file", KNOWN_TOOL_NAMES)

    def test_agent_new_task_resets_guard(self):
        class Model:
            def generate(self, messages, tools):
                if messages[-1]["role"] == "user":
                    return {"type": "tool_call", "id": "one", "name": "list_files", "arguments": {}}
                return {"type": "final", "content": "done"}
        agent = Agent(Model(), self.registry, self.context)
        for _ in range(3):
            result = agent.run("list")
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["messages"][2]["is_error"])

    def test_real_child_process_does_not_inherit_secret(self):
        code = "import os,json; print(json.dumps({k:os.environ.get(k) for k in ['DEEPSEEK_API_KEY','PYTHONPATH','TMP','PATH']}))"
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only", "PYTHONPATH": "test-only", "TMP": str(self.root)}):
            result = LocalEnvironment().execute(argv=[sys.executable, "-c", code], cwd=self.root, timeout_seconds=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertIsNone(values["DEEPSEEK_API_KEY"])
        self.assertIsNone(values["PYTHONPATH"])
        self.assertEqual(values["TMP"], str(self.root))
        self.assertIsNotNone(values["PATH"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
