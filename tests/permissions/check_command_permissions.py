import unittest

from forgepilot.permissions.bash_analysis import BashCommandAnalyzer
from forgepilot.permissions.command_checks import check_command_permissions
from forgepilot.permissions.models import ToolPermissionRequest
from forgepilot.permissions.tool_checks import ToolSafetyChecker


class CommandPermissionChecks(unittest.TestCase):
    def request(self, command, mode="default"):
        return ToolPermissionRequest(
            tool_name="run_command",
            arguments={"command": command},
            workspace="D:/program/ForgePilot",
            mode=mode,
        )

    def test_static_literals_and_dynamic_targets(self):
        analyzer = BashCommandAnalyzer()
        for target, expected in [
            ("output.txt", "output.txt"),
            ("'my output.txt'", "my output.txt"),
            ('"my output.txt"', "my output.txt"),
            ("'$OUT'", "$OUT"),
            ('"$OUT"', None),
            ('"$(echo output.txt)"', None),
            ("*.txt", None),
            ("~/output.txt", None),
            ("{a,b}.txt", None),
            (r"my\ output.txt", None),
        ]:
            with self.subTest(target=target):
                result = analyzer.analyze(f"echo hello > {target}")
                self.assertTrue(result.parsed)
                self.assertEqual(result.redirects[0].static_target, expected)

    def test_clear_violations_are_mandatory_in_every_mode(self):
        checker = ToolSafetyChecker()
        for mode in ("default", "accept_edits", "plan", "bypass", "auto", "bubble"):
            for target in (".env", "'.git/config'", '".forgepilot/state"', "../outside.txt", "/tmp/output.txt"):
                with self.subTest(mode=mode, target=target):
                    request = self.request(f"echo hello > {target}", mode)
                    self.assertEqual(checker.check_mandatory(request).action, "deny")
                    self.assertEqual(checker.check(request).action, "deny")

    def test_dynamic_target_does_not_hide_later_violation(self):
        result = check_command_permissions(
            self.request('echo hello > "$OUT"; echo hi > .env')
        )
        self.assertEqual(result.action, "deny")

    def test_descriptor_and_inline_input_are_not_paths(self):
        analyzer = BashCommandAnalyzer()
        for command, kind in [
            ("echo hello 2>&1", "descriptor"),
            ('cat <<< ".env"', "inline"),
            ("cat <<EOF\n.env\nEOF", "inline"),
        ]:
            with self.subTest(command=command):
                self.assertEqual(analyzer.analyze(command).redirects[0].kind, kind)
                self.assertEqual(check_command_permissions(self.request(command)).action, "abstain")

    def test_nested_redirection_is_checked(self):
        result = check_command_permissions(
            self.request('echo "$(echo hi > .env)"')
        )
        self.assertEqual(result.action, "deny")

    def test_no_command_is_automatically_allowed(self):
        for command in (
            "python -m unittest discover -v",
            "echo hello > output.txt",
            'echo hello > "$OUT"',
            "echo hello > /dev/null",
            "echo hello >&$FD",
            'echo "unterminated',
        ):
            with self.subTest(command=command):
                self.assertEqual(check_command_permissions(self.request(command)).action, "abstain")

    def test_file_operands_and_nested_commands(self):
        for command in (
            "cat .env",
            "rm -rf .git",
            "cp main.py .env",
            "head -n 20 .env",
            "python -m unittest && cat .env",
            'echo "$(cat .env)"',
            "cat main.py | tee .env",
            "cat ../outside.txt",
        ):
            with self.subTest(command=command):
                self.assertEqual(check_command_permissions(self.request(command)).action, "deny")

    def test_pattern_option_and_literal_text_are_not_mistaken_for_paths(self):
        for command in (
            "grep '.env' main.py",
            "echo '.env'",
            "head -n 10 main.py",
            "cat --unknown .env",
            'cat "$FILE"',
            '"$CMD" .env',
            "python -c 'print(1)'",
        ):
            with self.subTest(command=command):
                self.assertEqual(check_command_permissions(self.request(command)).action, "abstain")


if __name__ == "__main__":
    unittest.main(verbosity=2)
