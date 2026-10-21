"""自动 Skill 提取服务检查：仅使用脚本模型和临时目录。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.skills.check_skill_extractor import FakeExtractionModel
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.skills.extraction_service import SkillExtractionService
from forgepilot.skills.extractor import SkillExtractor
from forgepilot.skills.models import SkillDocument
from forgepilot.skills.store import SkillStore
import json

class ControlledModel(FakeExtractionModel):
    def __init__(self):
        super().__init__()
        self.fail_next = False
        self.fail_update = False
        self.on_generate = None
        self.updated_skill = None
        self.update_request = None

    def generate(self, *, messages, tools):
        name = tools[0]["name"]

        if name == "submit_skill_update":
            self.calls += 1
            self.update_request = json.loads(
                messages[1]["content"]
            )

            if self.fail_update:
                raise ModelError(
                    "test_update_failure",
                    "模拟更新请求失败",
                )

            response = {
                "type": "tool_call",
                "id": f"update_{self.calls}",
                "name": "submit_skill_update",
                "arguments": {
                    "updated_skill": self.updated_skill,
                    "reason": "脚本模型返回预设合并结果。",
                },
            }

        else:
            if self.fail_next:
                self.fail_next = False
                self.calls += 1
                raise ModelError(
                    "test_failure",
                    "模拟提取模型失败",
                )

            response = super().generate(
                messages=messages,
                tools=tools,
            )

        if self.on_generate is not None:
            self.on_generate()

        return response


class AutoExtractionChecks(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="forgepilot-skill-check-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sessions = SessionStore(self.root / "session.jsonl")
        self.header = SessionHeader(
            project_id="skill-test", owner_id="test-user", cwd=str(self.root),
        )
        self.sessions.create(self.header)
        self.store = SkillStore(
            self.root / "skills", known_tools={"read_file", "list_files"},
        )
        self.model = ControlledModel()
        self.model.candidate = {
            "metadata": {
                "name": "inspect_python",
                "description": "静态分析 Python 文件。",
                "tools": ["read_file"],
            },
            "content": (
                "## 适用条件\n静态分析 Python 文件。\n"
                "## 步骤\n读取相关文件并分析。\n"
                "## 验证\n说明静态分析依据，不虚构测试。"
            ),
        }
        self.allowed = {"read_file", "list_files"}
        self.service = self.new_service(self.sessions)

    def new_service(self, sessions):
        return SkillExtractionService(
            sessions=sessions,
            skill_store=self.store,
            extractor=SkillExtractor(
                model=self.model,
                context_budget=ContextBudget(
                    context_window=32000, max_output_tokens=2048,
                    safety_margin=2000,
                ),
            ),
        )

    def append_task(self, text="分析代码", *, with_tool=True, complete=True):
        user = self.sessions.append_message({"role": "user", "content": text})
        if with_tool:
            call_id = f"read_{user.id}"
            self.sessions.append_message({
                "role": "assistant", "type": "tool_call",
                "id": call_id, "name": "read_file",
                "arguments": {"path": "calculator.py"},
            })
            if complete:
                self.sessions.append_message({
                    "role": "tool", "tool_call_id": call_id,
                    "content": "def add(a, b): return a + b",
                    "is_error": False, "exit_code": None,
                })
        if complete:
            self.sessions.append_message({
                "role": "assistant", "type": "final",
                "content": "已完成静态分析，未执行测试。",
            })
        return user

    def process(self):
        return self.service.process_completed_task(allowed_tools=self.allowed)

    def test_save_latest_task_and_deduplicate_after_reopen(self):
        old = self.append_task("旧任务")
        current = self.append_task("当前任务")
        history_before = self.sessions.path.read_bytes()

        result = self.process()

        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["task_entry_id"], current.id)
        self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(self.store.read("inspect_python").metadata.tools, ["read_file"])
        source_ids = [item["id"] for item in self.model.request["entries"]]
        self.assertEqual(source_ids[0], current.id)
        self.assertNotIn(old.id, source_ids)
        self.assertEqual(self.sessions.path.read_bytes(), history_before)

        reopened = self.new_service(SessionStore(self.sessions.path))
        result = reopened.process_completed_task(allowed_tools=self.allowed)
        self.assertEqual(result["status"], "already_processed")
        self.assertEqual(self.model.calls, 1)

    def test_plain_chat_skips_model(self):
        self.append_task("普通问答", with_tool=False)
        self.assertEqual(self.process()["status"], "skipped")
        self.assertEqual(self.process()["status"], "already_processed")
        self.assertEqual(self.model.calls, 0)
        self.assertEqual(self.store.list_metadata(), [])

    def test_no_candidate_is_processed_without_file(self):
        self.append_task()
        self.model.candidate = None
        self.assertEqual(self.process()["status"], "no_skill")
        self.assertEqual(self.process()["status"], "already_processed")
        self.assertEqual(self.model.calls, 1)
        self.assertEqual(self.store.list_metadata(), [])

    def test_existing_skill_is_unchanged_without_improvement(self):
        original = SkillDocument.model_validate(
            self.model.candidate
        )
        original.content = "已有指南，应保持不变。"

        path = self.store.save(original)
        before = path.read_bytes()
        self.append_task()

        # updated_skill 默认是 None。
        self.assertEqual(
            self.process()["status"],
            "unchanged",
        )
        self.assertEqual(path.read_bytes(), before)

        self.assertEqual(
            self.process()["status"],
            "already_processed",
        )

        # 一次提取，一次更新判断，重复处理没有新请求。
        self.assertEqual(self.model.calls, 2)
    def test_model_failure_can_retry_without_changing_task(self):
        self.append_task()
        history_before = self.sessions.path.read_bytes()
        self.model.fail_next = True

        with self.assertRaises(ModelError):
            self.process()

        self.assertFalse(self.service.state_path.exists())
        self.assertEqual(self.store.list_metadata(), [])
        self.assertEqual(self.sessions.path.read_bytes(), history_before)
        self.assertEqual(self.process()["status"], "saved")
        self.assertEqual(self.model.calls, 2)

    def test_pending_tools_do_not_request_model(self):
        self.append_task(complete=False)
        with self.assertRaisesRegex(ValueError, "尚未完成"):
            self.process()
        self.assertEqual(self.model.calls, 0)
        self.assertFalse(self.service.state_path.exists())

    def test_branch_change_during_generation_does_not_publish(self):
        self.append_task("旧任务")
        old_leaf_id = self.sessions.get_current_branch()[-1].id
        self.append_task("新任务")
        self.model.on_generate = lambda: self.sessions.move_cursor(old_leaf_id)

        with self.assertRaisesRegex(ValueError, "会话位置已变化"):
            self.process()

        self.assertEqual(self.store.list_metadata(), [])
        self.assertFalse(self.service.state_path.exists())

    def test_same_name_skill_is_merged_and_saved(self):
        original = SkillDocument.model_validate(
            self.model.candidate
        )
        original.content = "读取相关文件，说明静态分析依据。"
        self.store.save(original)

        merged = original.model_copy(deep=True)
        merged.content += "\n补充：检查输入边界，说明未覆盖的情况。"
        self.model.updated_skill = merged.model_dump()

        task = self.append_task()

        result = self.process()

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["task_entry_id"], task.id)
        self.assertEqual(
            self.store.read("inspect_python"),
            merged,
        )

        # 更新模型收到旧指南和当前任务证据。
        request = self.model.update_request

        self.assertEqual(
            request["existing"],
            original.model_dump(),
        )
        self.assertEqual(
            request["entries"][0]["id"],
            task.id,
        )

        self.assertEqual(
            self.process()["status"],
            "already_processed",
        )
        self.assertEqual(self.model.calls, 2)

    def test_update_failure_keeps_old_file_and_can_retry(self):
        original = SkillDocument.model_validate(
            self.model.candidate
        )
        path = self.store.save(original)
        before = path.read_bytes()

        self.append_task()
        self.model.fail_update = True

        with self.assertRaises(ModelError):
            self.process()

        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.service.state_path.exists())

        # 更新失败没有标记完成，允许后续重新处理。
        self.model.fail_update = False

        self.assertEqual(
            self.process()["status"],
            "unchanged",
        )
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_update_does_not_overwrite_old_file(self):
        original = SkillDocument.model_validate(
            self.model.candidate
        )
        path = self.store.save(original)
        before = path.read_bytes()

        invalid = original.model_copy(deep=True)
        invalid.metadata.tools = ["run_command"]
        self.model.updated_skill = invalid.model_dump()

        self.append_task()

        with self.assertRaisesRegex(ValueError, "不允许的工具"):
            self.process()

        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.service.state_path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
