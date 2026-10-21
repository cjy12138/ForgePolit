from forgepilot.paths import PROJECT_ROOT
"""扩展工具离线验收：真实临时文件、临时 Git 仓库，不请求 API/Docker。"""
import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from forgepilot.contracts import ToolContext
from forgepilot.permissions.models import PermissionRule, ToolPermissionRequest
from forgepilot.permissions.rules import rule_matches
from forgepilot.skills.catalog import KNOWN_TOOL_NAMES
from forgepilot.skills.store import KNOWN_TOOL_NAMES as STORE_NAMES, SkillStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.extended import build_extended_tools


class ExtendedToolChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="forgepilot-extended-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.context = ToolContext(workspace=self.root)
        self.registry = ToolRegistry(build_extended_tools())
        self.registry.configure_permissions(mode="plan")
        (self.root / "src").mkdir()
        (self.root / "src/a.py").write_text("import os\nfrom pathlib import Path\nclass A:\n    def run(self):\n        return 1\n", encoding="utf-8")
        (self.root / "data.json").write_text('{"a/b": {"values": [1, 2]}}', encoding="utf-8")
        (self.root / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")

    def call(self, name, **args):
        result = self.registry.execute(name, args, self.context)
        self.assertTrue(result.ok, result.output)
        return result.output

    def test_catalog_and_individual_permission_checks(self):
        tools = build_extended_tools()
        self.assertEqual(len(tools), 15)
        self.assertEqual(len(KNOWN_TOOL_NAMES), 30)
        self.assertEqual(KNOWN_TOOL_NAMES, STORE_NAMES)
        for name, entry in tools.items():
            self.assertIn(name, KNOWN_TOOL_NAMES)
            self.assertTrue(callable(entry["check_permissions"]))
            entry["input_model"].model_json_schema()

    def test_documented_catalog_and_example_skill(self):
        project = PROJECT_ROOT
        document = (project / "docs/guides/tools-catalog.md").read_text(encoding="utf-8")
        names = set(re.findall(r"^### \d+\. ([a-z_]+)：", document, re.MULTILINE))
        self.assertEqual(names, KNOWN_TOOL_NAMES)
        store = SkillStore(self.root / "example-skills")
        store.import_file(project / "examples/skills/repository_inspection/SKILL.md")
        skill = store.read("repository_inspection")
        self.assertTrue(set(skill.metadata.tools) <= set(build_extended_tools()))

    def test_glob_tree_and_ignored_directories(self):
        (self.root / ".env").write_text("SECRET", encoding="utf-8")
        for name in (".hidden", ".venv", "node_modules"):
            (self.root / name).mkdir()
            (self.root / name / "hidden.py").write_text("secret", encoding="utf-8")
        files = json.loads(self.call("glob_files", pattern="**/*.py"))["files"]
        self.assertEqual(files, ["src/a.py"])
        self.assertEqual(json.loads(self.call("glob_files", path="./src", pattern="*.py"))["files"], files)
        tree = json.loads(self.call("directory_tree", max_depth=1))["entries"]
        self.assertTrue(any(item["path"] == "src" for item in tree))
        self.assertFalse(any(item["path"] == "src/a.py" for item in tree))
        self.assertNotIn("SECRET", self.call("read_files", paths=["src/a.py", "pyproject.toml"]))

    def test_info_hash_batch_compare(self):
        info = json.loads(self.call("file_info", path="data.json"))
        self.assertEqual(info["type"], "file")
        expected = hashlib.sha256((self.root / "data.json").read_bytes()).hexdigest()
        self.assertEqual(json.loads(self.call("file_hash", path="data.json"))["sha256"], expected)
        batch = json.loads(self.call("read_files", paths=["src/a.py", "data.json"], per_file_chars=8))
        self.assertEqual(len(batch["files"]), 2)
        self.assertTrue(batch["files"][0]["truncated"])
        self.assertIn("内容相同", self.call("compare_files", left="data.json", right="data.json"))
        self.assertIn("@@", self.call("compare_files", left="data.json", right="src/a.py"))

    def test_json_pointer_and_bad_content(self):
        self.assertEqual(json.loads(self.call("read_json", path="data.json", pointer="/a~1b/values/1")), 2)
        self.assertFalse(self.registry.execute("read_json", {"path": "data.json", "pointer": "/missing"}, self.context).ok)
        (self.root / "bad.json").write_text("{", encoding="utf-8")
        self.assertFalse(self.registry.execute("read_json", {"path": "bad.json"}, self.context).ok)

    def test_python_inspection_does_not_execute(self):
        path = self.root / "src/a.py"
        path.write_text(path.read_text(encoding="utf-8") + '\nraise RuntimeError("do not run")\n', encoding="utf-8")
        symbols = json.loads(self.call("python_outline", path="src/a.py"))["symbols"]
        self.assertEqual([item["name"] for item in symbols], ["A", "A.run"])
        self.assertEqual(len(json.loads(self.call("python_imports", path="src/a.py"))["imports"]), 2)
        self.assertTrue(json.loads(self.call("python_check", path="src/a.py"))["syntax_valid"])
        self.assertFalse((self.root / "src/__pycache__").exists())
        (self.root / "bad.py").write_text("def (", encoding="utf-8")
        self.assertFalse(json.loads(self.call("python_check", path="bad.py"))["syntax_valid"])
        self.assertFalse(self.registry.execute("python_outline", {"path": "bad.py"}, self.context).ok)
        self.assertIn("pyproject.toml", self.call("project_info"))

    def test_all_paths_checked_even_in_bypass(self):
        self.registry.configure_permissions(mode="bypass")
        (self.root / ".env").write_text("SECRET", encoding="utf-8")
        for name, args in [
            ("read_files", {"paths": ["src/a.py", ".env"]}),
            ("compare_files", {"left": "src/a.py", "right": ".env"}),
            ("file_hash", {"path": "../outside"}),
            ("git_show_file", {"path": ".git/config"}),
        ]:
            with self.subTest(name=name):
                result = self.registry.execute(name, args, self.context)
                self.assertFalse(result.ok)
                self.assertNotIn("SECRET", result.output)

    def test_multifile_rules(self):
        request = ToolPermissionRequest(tool_name="read_files", arguments={"paths": ["src/a.py", "data.json"]}, workspace=str(self.root))
        self.assertFalse(rule_matches(PermissionRule(action="allow", tool="read_files", pattern="src/*"), request))
        self.assertTrue(rule_matches(PermissionRule(action="deny", tool="read_files", pattern="src/*"), request))

    def test_invalid_schema_and_size_limits(self):
        for name, args in [
            ("read_files", {"paths": []}), ("read_files", {"paths": [1]}),
            ("directory_tree", {"max_depth": 100}), ("glob_files", {"max_results": 10000}),
        ]:
            self.assertFalse(self.registry.execute(name, args, self.context).ok)
        (self.root / "large").write_bytes(b"x" * (1024 * 1024 + 1))
        self.assertFalse(self.registry.execute("file_hash", {"path": "large"}, self.context).ok)
        (self.root / "binary").write_bytes(b"a\x00b")
        self.assertFalse(self.registry.execute("read_files", {"paths": ["binary"]}, self.context).ok)

    def test_symlink_escape(self):
        with TemporaryDirectory() as outside:
            target = Path(outside) / "secret"
            target.write_text("outside-secret", encoding="utf-8")
            try:
                (self.root / "link").symlink_to(target)
            except OSError:
                self.skipTest("Windows 未授予创建符号链接权限")
            self.assertFalse(self.registry.execute("read_files", {"paths": ["link"]}, self.context).ok)

    def test_skill_can_import_extended_names(self):
        source = self.root / "SKILL.md"
        source.write_text('---\nname: inspect_repo\ndescription: inspect repository\ntools:\n  - python_outline\n  - git_status\n---\nRead only.\n', encoding="utf-8")
        store = SkillStore(self.root / "skills")
        # 走真实 Skill 导入路径，确保目录与运行时工具名称一致。
        store.import_file(source)
        self.assertEqual(store.read("inspect_repo").metadata.tools, ["python_outline", "git_status"])


class GitQueryChecks(unittest.TestCase):
    call = ExtendedToolChecks.call
    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=Tool Test", "-c", "user.email=test@example.invalid", *args], cwd=self.root, check=True, capture_output=True).stdout

    def setUp(self):
        ExtendedToolChecks.setUp(self)
        self.git("init")
        self.git("add", "--", "src/a.py", "data.json", "pyproject.toml")
        self.git("commit", "-m", "baseline")

    def test_git_queries_and_no_index_mutation(self):
        original = (self.root / "src/a.py").read_text(encoding="utf-8")
        (self.root / "src/a.py").write_text(original + "# changed\n", encoding="utf-8")
        (self.root / ".env").write_text("SECRET", encoding="utf-8")
        head = self.git("rev-parse", "HEAD")
        index = (self.root / ".git/index").read_bytes()
        status = self.call("git_status")
        self.assertIn("src/a.py", status)
        self.assertNotIn(".env", status)
        self.assertIn("+# changed", self.call("git_diff", path="src/a.py"))
        self.assertIn("baseline", self.call("git_log", path="src/a.py"))
        self.assertEqual(self.call("git_show_file", path="src/a.py"), original)
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        self.assertEqual(index, (self.root / ".git/index").read_bytes())

    def test_staged_diff_and_protected_history(self):
        (self.root / "data.json").write_text('{"changed": true}', encoding="utf-8")
        self.git("add", "data.json")
        self.assertIn("changed", self.call("git_diff", path="data.json", staged=True))
        (self.root / ".env").write_text("HISTORIC_SECRET", encoding="utf-8")
        self.git("add", ".env")
        self.git("commit", "-m", "second")
        self.assertFalse(self.registry.execute("git_show_file", {"path": ".env"}, self.context).ok)
        self.assertNotIn("HISTORIC_SECRET", self.call("git_diff", path="*", staged=True))
        self.assertFalse(self.registry.execute("git_show_file", {"path": "data.json", "ref": "--help"}, self.context).ok)

    def test_external_diff_driver_not_executed(self):
        (self.root / ".gitattributes").write_text("*.py diff=unsafe\n", encoding="utf-8")
        # 若外部 driver 被启动，Git 会尝试运行不存在的命令并失败。
        self.git("config", "diff.unsafe.command", "forgepilot-nonexistent-external-driver")
        (self.root / "src/a.py").write_text("value = 2\n", encoding="utf-8")
        self.assertIn("+value = 2", self.call("git_diff", path="src/a.py"))

    def test_not_repository_and_parent_repository_refused(self):
        child = self.root / "src"
        result = self.registry.execute("git_status", {}, ToolContext(workspace=child))
        self.assertFalse(result.ok)
        with TemporaryDirectory() as outside:
            self.assertFalse(self.registry.execute("git_status", {}, ToolContext(workspace=Path(outside))).ok)

    def test_git_timeout_is_tool_error(self):
        with patch("forgepilot.tools.git_queries.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 15)):
            self.assertFalse(self.registry.execute("git_status", {}, self.context).ok)


if __name__ == "__main__":
    unittest.main()
