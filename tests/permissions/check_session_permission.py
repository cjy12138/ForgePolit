import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.models import (
    PermissionRule, PermissionSettings, RuleLayer, ToolPermissionRequest,
)
from forgepilot.permissions.operation import hash_permission_operation


class SessionPermissionChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="forgepilot-session-permission-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.request = ToolPermissionRequest(
            tool_name="run_python", workspace=str(self.workspace),
            arguments={"args": ["-m", "unittest"], "timeout_seconds": 15},
        )

    def test_grant_is_exact_and_deduplicated(self):
        engine = PermissionEngine(PermissionSettings())
        self.assertEqual(engine.evaluate(self.request).action, "ask")
        engine.add_session_grant(self.request)
        engine.add_session_grant(self.request)
        self.assertEqual(engine.evaluate(self.request).action, "allow")
        self.assertEqual(len(engine.settings.layers[-1].rules), 1)
        for change in (
            {"arguments": {"args": ["-c", "print(1)"], "timeout_seconds": 15}},
            {"arguments": {"args": ["-m", "unittest"], "timeout_seconds": 60}},
            {"workspace": str(self.workspace / "other-task")},
            {"tool_name": "run_command", "arguments": {"command": "echo hello"}},
        ):
            with self.subTest(change=change):
                self.assertEqual(engine.evaluate(self.request.model_copy(deep=True, update=change)).action, "ask")

    def test_dictionary_order_does_not_change_fingerprint(self):
        reordered = self.request.model_copy(deep=True, update={"arguments": {
            "timeout_seconds": 15, "args": ["-m", "unittest"],
        }})
        self.assertEqual(hash_permission_operation(self.request), hash_permission_operation(reordered))

    def test_existing_deny_cannot_be_overridden(self):
        for source in ("policy", "user", "project", "local", "cli", "session"):
            with self.subTest(source=source):
                engine = PermissionEngine(PermissionSettings(layers=[RuleLayer(
                    source=source, rules=[PermissionRule(action="deny", tool="run_python")],
                )]))
                engine.add_session_grant(self.request)
                self.assertEqual(engine.evaluate(self.request).action, "deny")

    def test_grant_cannot_bypass_plan_or_protected_path(self):
        plan = PermissionEngine(PermissionSettings(mode="plan"))
        plan.add_session_grant(self.request)
        self.assertEqual(plan.evaluate(self.request).action, "deny")
        protected = ToolPermissionRequest(
            tool_name="run_command", workspace=str(self.workspace),
            arguments={"command": "cat .env"},
        )
        engine = PermissionEngine(PermissionSettings(mode="bypass"))
        engine.add_session_grant(protected)
        self.assertEqual(engine.evaluate(protected).action, "deny")

    def test_other_engine_does_not_inherit_grant(self):
        settings = PermissionSettings()
        first = PermissionEngine(settings)
        second = PermissionEngine(settings)
        first.add_session_grant(self.request)
        self.assertEqual(first.evaluate(self.request).action, "allow")
        self.assertEqual(second.evaluate(self.request).action, "ask")
        self.assertEqual(settings.layers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
