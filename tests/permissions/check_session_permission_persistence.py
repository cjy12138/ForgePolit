import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from forgepilot.permissions.approval import confirm_tool_permission
from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.models import (
    PermissionRule, PermissionSettings, RuleLayer, ToolPermissionRequest,
)
from forgepilot.permissions.service import PermissionService
from forgepilot.permissions.session_state import SessionPermissionState


class SessionPermissionPersistenceChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="forgepilot-session-grants-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "session.permissions.json"
        self.request = ToolPermissionRequest(
            tool_name="run_python", workspace=str(self.root),
            arguments={"args": ["-c", "print('private-value')"], "timeout_seconds": 15},
        )
        self.template = PermissionService(PermissionEngine(PermissionSettings()))

    def bind(self, *, template=None, session_id="session-one", path=None, workspace=None):
        return (template or self.template).for_session(
            path=path or self.path, session_id=session_id, workspace=workspace or self.root,
        )

    def authorize(self, service, answer="s", request=None):
        with redirect_stdout(StringIO()), patch("builtins.input", return_value=answer) as asked:
            result = service.authorize(request or self.request)
        return result, asked.call_count

    def test_s_is_saved_and_reused_after_reopening(self):
        first, calls = self.authorize(self.bind())
        self.assertEqual((first.action, calls), ("allow", 1))
        self.assertTrue(first.remember_session)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("private-value", text)
        self.assertEqual(len(json.loads(text)["rules"]), 1)
        second, calls = self.authorize(self.bind(), answer="n")
        self.assertEqual((second.action, calls), ("allow", 0))
        self.assertEqual(second.source, "rule")

    def test_y_is_only_for_one_call_and_n_denies(self):
        service = self.bind()
        first, calls = self.authorize(service, answer="y")
        self.assertEqual((first.action, calls), ("allow", 1))
        self.assertFalse(self.path.exists())
        second, calls = self.authorize(service, answer="n")
        self.assertEqual((second.action, calls), ("deny", 1))

    def test_changed_arguments_and_new_session_need_confirmation(self):
        self.authorize(self.bind())
        changed = self.request.model_copy(deep=True, update={"arguments": {
            "args": ["-m", "unittest"], "timeout_seconds": 15,
        }})
        result, calls = self.authorize(self.bind(), answer="n", request=changed)
        self.assertEqual((result.action, calls), ("deny", 1))
        other = self.bind(session_id="session-two", path=self.root / "other.json")
        result, calls = self.authorize(other, answer="n")
        self.assertEqual((result.action, calls), ("deny", 1))

    def test_identity_mismatch_and_broad_state_are_rejected(self):
        self.authorize(self.bind())
        with self.assertRaises(ValueError):
            self.bind(session_id="different-session")
        with self.assertRaises(ValueError):
            self.bind(workspace=self.root / "different-workspace")
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["rules"][0]["operation_sha256"] = None
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.bind()

    def test_restored_grant_cannot_override_new_deny_or_plan(self):
        self.authorize(self.bind())
        for settings in (
            PermissionSettings(mode="plan"),
            PermissionSettings(layers=[RuleLayer(source="policy", rules=[
                PermissionRule(action="deny", tool="run_python"),
            ])]),
        ):
            template = PermissionService(PermissionEngine(settings))
            result, calls = self.authorize(self.bind(template=template), answer="y")
            self.assertEqual((result.action, calls), ("deny", 0))

    def test_forked_task_does_not_copy_coding_grants(self):
        coding = self.bind()
        self.authorize(coding)
        task_workspace = self.root / "task-copy"
        task_workspace.mkdir()
        bugfix = coding.for_session(
            path=self.root / "bugfix-state.json", session_id="bugfix-session", workspace=task_workspace,
        )
        task_request = self.request.model_copy(deep=True, update={"workspace": str(task_workspace)})
        result, calls = self.authorize(bugfix, answer="n", request=task_request)
        self.assertEqual((result.action, calls), ("deny", 1))
        self.assertEqual(self.authorize(coding, answer="n")[1], 0)

    def test_save_failure_preserves_only_explicit_one_time_approval(self):
        service = self.bind()
        with patch.object(service.session_state, "add", side_effect=OSError("disk unavailable")):
            result, calls = self.authorize(service)
        self.assertEqual((result.action, calls), ("allow", 1))
        self.assertFalse(getattr(result, "remember_session", False))
        self.assertFalse(self.path.exists())
        result, calls = self.authorize(service, answer="n")
        self.assertEqual((result.action, calls), ("deny", 1))

    def test_state_add_is_deduplicated(self):
        state = SessionPermissionState(self.path, session_id="session-one", workspace=self.root)
        state.add(self.request)
        state.add(self.request)
        self.assertEqual(len(state.load()), 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
