from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.sessions.context import build_messages
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


class FakeTools:
    def __init__(self, store):
        self.store = store
        self.executions = 0

    def definitions(self):
        return []

    def execute(self, name, arguments, *, context):
        # 工具开始执行时，调用记录必须已经保存。
        branch = self.store.get_current_branch()
        assert branch[-1].payload["type"] == "tool_call"
        assert branch[-1].payload["id"] == "call_a"

        self.executions += 1
        return ToolResult(ok=True, output="项目说明")


class FirstModel:
    def generate(self, messages, tools):
        if messages[-1]["role"] == "user":
            return {
                "type": "tool_call",
                "id": "call_a",
                "name": "read_file",
                "arguments": {"path": "README.md"},
            }

        assert messages[-1]["role"] == "tool"

        return {
            "type": "final",
            "content": "已读取项目说明。",
        }


class FollowupModel:
    def generate(self, messages, tools):
        # 重新创建 Agent 后，仍应收到上一轮完整历史。
        assert [message["role"] for message in messages] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "user",
        ]

        assert messages[0]["content"] == "读取项目说明"
        assert messages[-1]["content"] == "刚才读到了什么？"

        return {
            "type": "final",
            "content": "刚才读到了项目说明。",
        }


def main():
    with TemporaryDirectory() as directory:
        workspace = Path(directory)
        path = workspace / "session.jsonl"

        store = SessionStore(path)
        store.create(SessionHeader(
            project_id="demo-project",
            owner_id="demo-user",
            cwd=directory,
        ))

        tools = FakeTools(store)

        agent = Agent(
            FirstModel(),
            tools,
            ToolContext(workspace),
            session_store=store,
        )

        result = agent.run("读取项目说明")

        assert result["status"] == "completed"
        assert result["n_calls"] == 2
        assert tools.executions == 1

        saved = build_messages(store.get_current_branch())
        assert saved == result["messages"]
        assert len(saved) == 4

        print("调用先保存再执行，整轮消息完整落盘：通过")

        reopened = SessionStore(path)

        new_agent = Agent(
            FollowupModel(),
            tools,
            ToolContext(workspace),
            session_store=reopened,
        )

        followup = new_agent.run("刚才读到了什么？")

        assert followup["status"] == "completed"
        assert followup["n_calls"] == 1
        assert tools.executions == 1

        saved = build_messages(reopened.get_current_branch())
        assert saved == followup["messages"]
        assert len(saved) == 6

        print("重新创建 Agent 后加载历史，且不重复执行旧工具：通过")


if __name__ == "__main__":
    main()