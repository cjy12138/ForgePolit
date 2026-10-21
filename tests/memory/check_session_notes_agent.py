import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.memory.notes import render_session_notes
from forgepilot.memory.notes_agent import SessionNotesAgent
from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


class FakeNotesModel:
    max_output_tokens = 1024

    def __init__(self):
        self.calls = 0
        self.invalid_source = False
        self.estimated_tokens = 100

    def estimate_input_tokens(self, *, messages, tools):
        return self.estimated_tokens

    def generate(self, *, messages, tools):
        self.calls += 1

        assert [tool["name"] for tool in tools] == [
            "submit_session_notes"
        ]

        data = json.loads(messages[1]["content"])
        entries = data["entries"]

        source_ids = [
            entries[0]["id"],
            entries[-1]["id"],
        ]

        if self.invalid_source:
            source_ids = ["missing-entry"]

        return {
            "type": "tool_call",
            "id": "notes_call",
            "name": "submit_session_notes",
            "arguments": {
                "goal": "修复 calculator.py 的 add 函数。",
                "constraints": ["修改前先读取文件。"],
                "progress": ["已读取 calculator.py，尚未修改。"],
                "files": ["calculator.py：待检查的计算器实现。"],
                "verification": ["尚未运行测试。"],
                "next_steps": ["检查实现，再决定是否修改。"],
                "source_entry_ids": source_ids,
            },
        }


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        store = SessionStore(root / "session.jsonl")

        store.create(SessionHeader(
            owner_id="local-user",
            project_id="demo",
            cwd=str(root),
        ))

        store.append_message({
            "role": "user",
            "content": (
                "修复 calculator.py 的 add 函数，"
                "修改前先读取文件。"
            ),
        })

        store.append_message({
            "role": "assistant",
            "type": "tool_call",
            "id": "read_a",
            "name": "read_file",
            "arguments": {"path": "calculator.py"},
        })

        # 工具结果还没回来，不能创建笔记快照。
        try:
            build_memory_snapshot(
                store,
                require_final=False,
            )
        except ValueError as exc:
            assert "未完成的工具调用" in str(exc)
        else:
            raise AssertionError("应拒绝不完整工具交互")

        print("工具结果未收齐时拒绝：通过")

        store.append_message({
            "role": "tool",
            "tool_call_id": "read_a",
            "content": "def add(a, b):\n    return a - b",
            "is_error": False,
            "exit_code": None,
        })

        # 没有最终回答，但工具交互已经完整。
        snapshot = build_memory_snapshot(
            store,
            require_final=False,
        )
        before = store.path.read_bytes()

        model = FakeNotesModel()
        agent = SessionNotesAgent(
            model=model,
            context_budget=ContextBudget(
                context_window=16000,
                max_output_tokens=1024,
                safety_margin=1000,
            ),
        )

        notes = agent.generate(snapshot)
        markdown = render_session_notes(notes)

        assert "已读取 calculator.py，尚未修改" in markdown
        assert "尚未运行测试" in markdown
        assert "## 下一步" in markdown
        assert model.calls == 1
        assert store.path.read_bytes() == before

        print("任务进行中生成笔记，原历史不变：通过")

        model.invalid_source = True

        try:
            agent.generate(snapshot)
        except ValueError as exc:
            assert "不存在的来源节点" in str(exc)
        else:
            raise AssertionError("应拒绝编造来源")

        print("拒绝不存在的来源节点：通过")

        model.invalid_source = False
        model.estimated_tokens = 100000
        calls_before = model.calls

        try:
            agent.generate(snapshot)
        except ModelError as exc:
            assert exc.code == "session_notes_budget_exceeded"
        else:
            raise AssertionError("应拒绝超预算请求")

        assert model.calls == calls_before
        print("输入超预算时不发送模型请求：通过")


if __name__ == "__main__":
    main()