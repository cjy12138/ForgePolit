"""验收临时规则、持久规则管理和优先级；只使用临时目录。"""
import argparse
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.permissions.__main__ import main
from forgepilot.permissions.cli_args import add_permission_rule_arguments
from forgepilot.permissions.config import load_permission_settings
from forgepilot.permissions.models import PermissionRule, ToolPermissionRequest
from forgepilot.permissions.rule_store import PermissionRuleStore
from forgepilot.permissions.rules import RuleEngine


class PermissionCLIChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="forgepilot-permission-cli-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.policy = self.workspace / "policy.json"
        self.user = self.workspace / "user.json"
        self.store = PermissionRuleStore(
            workspace=self.workspace, policy_path=self.policy, user_path=self.user,
        )

    def settings(self, **kwargs):
        return load_permission_settings(
            workspace=self.workspace, policy_path=self.policy, user_path=self.user, **kwargs,
        )

    def request(self):
        return ToolPermissionRequest(
            tool_name="run_python", arguments={"args": ["-m", "unittest", "discover", "-v"]},
            workspace=str(self.workspace),
        )

    def test_add_duplicate_and_remove_preserve_other_rules(self):
        first = PermissionRule(action="allow", tool="run_python", pattern="python -m unittest discover -v")
        second = PermissionRule(action="deny", tool="edit_file", pattern="test_*.py")
        self.assertEqual(self.store.add("local", first), (1, True))
        before = self.store.path_for("local").read_bytes()
        self.assertEqual(self.store.add("local", first), (1, False))
        self.assertEqual(self.store.path_for("local").read_bytes(), before)
        self.assertEqual(self.store.add("local", second), (2, True))
        self.assertEqual(self.store.remove("local", 1), first)
        self.assertEqual(self.store.list_rules("local"), [second])
        self.assertEqual(list(self.store.path_for("local").parent.glob("*.tmp")), [])

    def test_invalid_index_and_malformed_config_do_not_change_file(self):
        rule = PermissionRule(action="deny", tool="run_command")
        self.store.add("project", rule)
        path = self.store.path_for("project")
        before = path.read_bytes()
        for index in (0, -1, 99):
            with self.assertRaises(ValueError):
                self.store.remove("project", index)
            self.assertEqual(path.read_bytes(), before)
        path.write_text('{"unexpected": true}', encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.add("project", rule)
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.settings()

    def test_policy_cannot_be_modified(self):
        self.policy.write_text('{"rules": []}', encoding="utf-8")
        before = self.policy.read_bytes()
        for operation in (
            lambda: self.store.add("policy", PermissionRule(action="allow", tool="*")),
            lambda: self.store.remove("policy", 1),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(self.policy.read_bytes(), before)

    def test_cli_rules_are_ephemeral_and_parse_repeated_flags(self):
        parser = argparse.ArgumentParser()
        add_permission_rule_arguments(parser)
        args = parser.parse_args([
            "--allow", "run_python", "python -m unittest discover -v",
            "--deny", "run_command", "--ask", "edit_file", "*.py",
        ])
        self.assertEqual([rule.action for rule in args.permission_rules], ["allow", "deny", "ask"])
        settings = self.settings(cli_rules=args.permission_rules)
        cli = next(layer for layer in settings.layers if layer.source == "cli")
        self.assertEqual(len(cli.rules), 3)
        self.assertEqual(RuleEngine(settings).evaluate(self.request()).action, "allow")
        self.assertTrue(all(not path.exists() for path in self.store.paths.values()))
        empty = parser.parse_args([])
        self.assertIsNone(empty.permission_rules)
        self.assertEqual(RuleEngine(self.settings()).evaluate(self.request()).action, "abstain")

    def test_invalid_startup_rule_is_rejected(self):
        parser = argparse.ArgumentParser()
        add_permission_rule_arguments(parser)
        with redirect_stderr(StringIO()):
            for flags in (["--allow"], ["--allow", "tool", "pattern", "extra"], ["--deny", ""]):
                with self.subTest(flags=flags), self.assertRaises(SystemExit):
                    parser.parse_args(flags)

    def test_deny_across_sources_and_source_priority(self):
        allow = PermissionRule(action="allow", tool="run_python")
        deny = PermissionRule(action="deny", tool="run_python")
        self.store.add("project", allow)
        self.assertEqual(RuleEngine(self.settings(cli_rules=[deny])).evaluate(self.request()).action, "deny")
        self.store.add("local", deny)
        self.assertEqual(RuleEngine(self.settings(cli_rules=[allow])).evaluate(self.request()).action, "deny")
        self.store.remove("local", 1)
        self.store.add("user", PermissionRule(action="ask", tool="run_python"))
        result = RuleEngine(self.settings(cli_rules=[allow])).evaluate(self.request())
        self.assertEqual(result.action, "abstain")
        self.assertEqual(result.rule_source, "user")

    def test_management_cli_round_trip(self):
        base = ["--workspace", str(self.workspace)]
        with redirect_stdout(StringIO()):
            self.assertEqual(main(base + ["add", "--source", "local", "--action", "deny", "--tool", "edit_file", "--pattern", "test_*.py"]), 0)
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(base + ["list", "--source", "local"]), 0)
        self.assertIn("1. deny edit_file", output.getvalue())
        with redirect_stdout(StringIO()):
            self.assertEqual(main(base + ["remove", "--source", "local", "1"]), 0)
        self.assertEqual(self.store.list_rules("local"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
