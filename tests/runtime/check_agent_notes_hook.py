from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.exceptions import ModelError
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import pending_tool_calls
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools


class FakeBusinessModel:
    def __init__(self):
        self.calls = 0

    def generate(self, *, messages, tools):
        self.calls += 1

        if self.calls == 1:
            return {
                "type": "tool_batch",
                "calls": [
                    {
                        "type": "tool_call",
                        "id": "read_a",
                        "name": "read_file",
                        "arguments": {"path": "a.txt"},
                    },
                    {
                        "type": "tool_call",
                        "id": "read_b",
                        "name": "read_file",
                        "arguments": {"path": "b.txt"},
                    },
                ],
            }

        if self.calls == 2:
            return {
                "type": "final",
                "content": "两个文件已经读取完成。",
            }

        raise AssertionError("不应再请求业务模型")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        (root / "a.txt").write_text(
            "文件 A",
            encoding="utf-8",
        )
        (root / "b.txt").write_text(
            "文件 B",
            encoding="utf-8",
        )

        sessions = SessionStore(root / "session.jsonl")
        sessions.create(SessionHeader(
            owner_id="local-user",
            project_id="demo",
            cwd=str(root),
        ))

        observations = []

        def notes_hook():
            branch = sessions.get_current_branch()

            # 回调发生时，不能还有未完成工具调用。
            assert pending_tool_calls(branch) == set()

            messages = [
                entry.payload
                for entry in branch
                if hasattr(entry, "payload")
            ]

            tool_count = sum(
                message.get("role") == "tool"
                for message in messages
            )

            observations.append((
                messages[-1]["role"],
                tool_count,
            ))

            # 故意模拟笔记生成失败。
            raise ModelError(
                "notes_test_failure",
                "假模型模拟笔记失败。",
            )

        model = FakeBusinessModel()

        agent = Agent(
            model=model,
            tools=ToolRegistry(build_file_tools()),
            context=ToolContext(workspace=root),
            max_steps=3,
            session_store=sessions,
            notes_hook=notes_hook,
        )

        result = agent.run("读取 a.txt 和 b.txt。")

        assert result["status"] == "completed"
        assert result["answer"] == "两个文件已经读取完成。"
        assert model.calls == 2
        assert result["n_calls"] == 2

        assert observations == [
            ("tool", 2),
            ("assistant", 2),
        ]

        assert agent.last_notes_result["status"] == "failed"

        print("整批结果收齐后只检查一次：通过")
        print("笔记失败不改变业务完成状态：通过")

        # 已完成会话的恢复也检查一次，但不请求业务模型。
        result = agent.resume()

        assert result["status"] == "completed"
        assert result["n_calls"] == 0
        assert model.calls == 2
        assert len(observations) == 3

        print("恢复已完成会话时检查笔记：通过")


if __name__ == "__main__":
    main()