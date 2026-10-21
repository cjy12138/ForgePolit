import unittest
from itertools import combinations

from forgepilot.permissions.models import (
    PermissionRule,
    PermissionSettings,
    RuleLayer,
    ToolPermissionRequest,
)
from forgepilot.permissions.rules import RuleEngine


class PermissionRuleChecks(unittest.TestCase):
    def request(self, tool="run_python", arguments=None, mode="default"):
        return ToolPermissionRequest(
            tool_name=tool,
            arguments=(
                {"args": ["-m", "unittest", "discover", "-v"]}
                if arguments is None
                else arguments
            ),
            workspace=r"D:\program\ForgePilot",
            mode=mode,
        )

    def layer(self, source, action, tool="run_python", pattern=None):
        return RuleLayer(
            source=source,
            rules=[
                PermissionRule(
                    action=action,
                    tool=tool,
                    pattern=pattern,
                ),
            ],
        )

    def evaluate(self, layers, request=None):
        return RuleEngine(
            PermissionSettings(layers=layers)
        ).evaluate(
            self.request() if request is None else request
        )

    def test_deny_wins_in_every_mode(self):
        layers = [
            self.layer("policy", "allow"),
            self.layer("session", "deny"),
        ]

        for mode in (
            "default",
            "accept_edits",
            "plan",
            "bypass",
            "auto",
            "bubble",
        ):
            with self.subTest(mode=mode):
                result = self.evaluate(
                    layers,
                    self.request(mode=mode),
                )
                self.assertEqual(result.action, "deny")
                self.assertEqual(result.rule_source, "session")

    def test_all_six_source_priorities(self):
        priority = (
            "policy",
            "user",
            "project",
            "local",
            "cli",
            "session",
        )

        for higher, lower in combinations(priority, 2):
            with self.subTest(higher=higher, lower=lower):
                # 故意把低优先级来源放在列表前面。
                result = self.evaluate([
                    self.layer(lower, "allow"),
                    self.layer(higher, "ask"),
                ])

                self.assertEqual(result.action, "abstain")
                self.assertEqual(result.rule_source, higher)
                self.assertEqual(
                    result.matched_rule.action,
                    "ask",
                )

    def test_ask_wins_within_same_source(self):
        for actions in (("allow", "ask"), ("ask", "allow")):
            with self.subTest(actions=actions):
                layer = RuleLayer(
                    source="project",
                    rules=[
                        PermissionRule(
                            action=action,
                            tool="run_python",
                        )
                        for action in actions
                    ],
                )

                result = self.evaluate([layer])

                self.assertEqual(result.action, "abstain")
                self.assertEqual(
                    result.matched_rule.action,
                    "ask",
                )

    def test_no_match_continues_pipeline(self):
        result = self.evaluate([
            self.layer("policy", "deny", tool="edit_file"),
        ])

        self.assertEqual(result.action, "abstain")
        self.assertIsNone(result.matched_rule)

    def test_path_matching_and_normalization(self):
        layers = [
            self.layer(
                "project",
                "allow",
                tool="read_file",
                pattern="src/*.py",
            ),
        ]

        for path in (
            "src/main.py",
            r"src\main.py",
            "./src/main.py",
            "src/nested/main.py",
        ):
            with self.subTest(path=path):
                result = self.evaluate(
                    layers,
                    self.request(
                        tool="read_file",
                        arguments={"path": path},
                    ),
                )
                self.assertEqual(result.action, "allow")

        result = self.evaluate(
            layers,
            self.request(
                tool="read_file",
                arguments={"path": "tests/test_main.py"},
            ),
        )
        self.assertEqual(result.action, "abstain")

    def test_python_and_shell_match_correct_fields(self):
        python_rule = self.layer(
            "project",
            "allow",
            pattern="python -m unittest*",
        )
        self.assertEqual(
            self.evaluate([python_rule]).action,
            "allow",
        )

        shell_rule = self.layer(
            "project",
            "allow",
            tool="run_command",
            pattern="python -m unittest discover -v",
        )

        result = self.evaluate(
            [shell_rule],
            self.request(
                tool="run_command",
                arguments={
                    "command": "python -m unittest discover -v",
                },
            ),
        )
        self.assertEqual(result.action, "allow")

        # 相同文本出现在其他字段，不能让命令规则命中。
        result = self.evaluate(
            [shell_rule],
            self.request(
                tool="run_command",
                arguments={
                    "command": "echo hello",
                    "note": "python -m unittest discover -v",
                },
            ),
        )
        self.assertEqual(result.action, "abstain")

    def test_tool_wildcard_without_content_pattern(self):
        result = self.evaluate(
            [self.layer("user", "allow", tool="read_*")],
            self.request(
                tool="read_file",
                arguments={"path": "README.md"},
            ),
        )
        self.assertEqual(result.action, "allow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
