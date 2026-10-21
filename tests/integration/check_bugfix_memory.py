"""验证共享 Markdown 记忆与任务独立召回状态，不调用 API。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.bugfix.check_bugfix_workspace import git
from tests.memory.check_memory_recall import SelectionModel
from tests.memory.check_memory_service import FakeModel
from forgepilot.bugfix_service import BugfixService
from forgepilot.memory.models import MemoryDocument, MemoryMetadata
from forgepilot.memory.selector import MemorySelector
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.memory.extraction import MemoryExtractionAgent
from forgepilot.memory.service import MemoryExtractionService
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.store import SessionStore


class MemoryCheckModel:
    max_output_tokens = 2048

    def __init__(self):
        self.calls = 0

    def estimate_input_tokens(self, *, messages, tools):
        return 1000

    def generate(self, *, messages, tools):
        names = {tool["name"] for tool in tools}
        assert {"read_memory", "glob_memory", "grep_memory", "read_memory_version"} <= names
        assert any(
            message.get("role") == "system"
            and "长期记忆快照" in message.get("content", "")
            and "只修业务代码，不修改测试文件" in message["content"]
            for message in messages
        )
        self.calls += 1
        if self.calls == 1:
            return {
                "type": "tool_call", "id": "read_rule", "name": "read_memory",
                "arguments": {"filename": "testing_rules.md", "offset": 0, "limit": 100},
            }
        assert self.calls == 2
        assert json.loads(messages[-1]["content"])["content"] == "只修业务代码，不修改测试文件。"
        return {"type": "final", "content": "已读取项目约定，未修改代码。"}


def main():
    with TemporaryDirectory(prefix="forgepilot-bugfix-memory-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        (source / "README.md").write_text("demo\n", encoding="utf-8")
        git(source, "init")
        git(source, "add", ".")
        git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
        memory = MarkdownMemoryStore(root / "memory")
        memory.save(MemoryDocument(
            filename="testing_rules.md",
            metadata=MemoryMetadata(
                name="测试约定", type="project", description="修复时的测试文件约定",
                source_session_id="coding-session", source_entry_ids=["source-entry"],
            ),
            content="只修业务代码，不修改测试文件。",
        ))
        original_memory = (memory.root / "testing_rules.md").read_bytes()
        selection_model = SelectionModel(["testing_rules.md"])
        selector = MemorySelector(
            selection_model,
            context_budget=ContextBudget(context_window=32000, max_output_tokens=512, safety_margin=2000),
        )
        states = []
        extraction_model = FakeModel()
        extraction_budget = ContextBudget(context_window=32000, max_output_tokens=1024, safety_margin=2000)
        for number in (1, 2):
            ticket = f"任务{number}：读取修复时的测试约定，不修改文件。"
            if number == 1:
                ticket += "以后编写 Python 测试时优先使用 unittest。"
            else:
                selection_model.names = ["testing_rules.md", "testing_preferences.md"]
            result = BugfixService(
                model=MemoryCheckModel(), source=source, tasks_root=root / "tasks",
                budget=ContextBudget(context_window=32000, max_output_tokens=2048, safety_margin=2000),
                memory_store=memory, memory_selector=selector,
                memory_extraction_model=extraction_model if number == 1 else None,
                memory_extraction_budget=extraction_budget if number == 1 else None,
            ).run(ticket)
            assert result["status"] == "completed", result
            assert selection_model.calls == number  # 工具循环没有重复召回。
            assert ticket in selection_model.request["question"]
            run_dir = Path(result["run_dir"])
            state = json.loads((run_dir / "memory-recall.json").read_text(encoding="utf-8"))
            header = SessionStore(run_dir / "session.jsonl").read()[0]
            assert state["session_id"] == header.session_id
            assert len(state["turns"]) == 1
            expected_bytes = 2 * len("只修业务代码，不修改测试文件。".encode("utf-8"))
            if number == 2:
                expected_bytes += len(memory.read("testing_preferences.md").content.encode("utf-8"))
                documents = next(iter(state["turns"].values()))
                assert {document["filename"] for document in documents} == {
                    "testing_rules.md", "testing_preferences.md",
                }
            assert state["used_bytes"] == expected_bytes
            if number == 1:
                assert result["memory_extraction"]["status"] == "published"
                document = memory.read("testing_preferences.md")
                assert document.metadata.source_session_id == header.session_id
                assert "testing_preferences.md" in memory.get_index_context()["indexed_filenames"]
                service = MemoryExtractionService(
                    sessions=SessionStore(run_dir / "session.jsonl"), memory_store=memory,
                    agent=MemoryExtractionAgent(
                        model=extraction_model, memory_store=memory, context_budget=extraction_budget,
                    ),
                )
                assert service.process_completed_turn()["status"] == "already_processed"
                assert extraction_model.calls == 1
            states.append(state)
        assert states[0]["session_id"] != states[1]["session_id"]
        assert (memory.root / "testing_rules.md").read_bytes() == original_memory
        git(source, "diff", "--exit-code", "HEAD", "--")
        print("共享记忆库，按各自 Ticket 召回且同轮复用：通过")
        print("记忆工具补读计入任务独立额度，两个任务状态互不混入：通过")
        print("原有主题内容与原工作区未修改：通过")
        print("从 bugfix 会话提取并更新主题和索引，下一任务可召回：通过")
        print("同一完成轮次不重复提取：通过")

        class FailingExtractionModel(FakeModel):
            def generate(self, *, messages, tools):
                raise ModelError("test_failure", "模拟提取失败")

        failed_memory = BugfixService(
            model=MemoryCheckModel(), source=source, tasks_root=root / "tasks",
            budget=ContextBudget(context_window=32000, max_output_tokens=2048, safety_margin=2000),
            memory_store=memory, memory_selector=selector,
            memory_extraction_model=FailingExtractionModel(), memory_extraction_budget=extraction_budget,
        ).run("读取已有测试约定，不修改文件。")
        assert failed_memory["status"] == "completed"
        assert failed_memory["memory_extraction"]["status"] == "failed"
        assert Path(failed_memory["delivery"]["report_path"]).exists()
        assert (Path(failed_memory["run_dir"]) / "memory-result.json").exists()
        print("记忆提取失败不改变修复状态，交付物仍保存：通过")


if __name__ == "__main__":
    main()
