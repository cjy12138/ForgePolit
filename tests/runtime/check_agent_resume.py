from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


class FakeTools:
    def __init__(self):
        self.executions = 0

    def definitions(self):
        return []

    def execute(self, name, arguments, *, context):
        self.executions += 1
        return ToolResult(ok=True, output="文件内容")


class FakeModel:
    def __init__(self):
        self.calls = 0

    def generate(self, messages, tools):
        self.calls += 1

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
            "content": "已根据文件内容完成分析。",
        }


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "session.jsonl"
        context = ToolContext(Path(directory))

        store = SessionStore(path)
        store.create(SessionHeader(
            project_id="demo-project",
            owner_id="demo-user",
            cwd=directory,
        ))

        tools = FakeTools()
        first_model = FakeModel()

        agent = Agent(
            first_model,
            tools,
            context,
            max_steps=1,
            session_store=store,
        )

        result = agent.run("读取并分析 README.md")

        assert result["status"] == "step_limit"
        assert tools.executions == 1

        assert [
            entry.payload["role"]
            for entry in store.get_current_branch()
        ] == ["user", "assistant", "tool"]

        print("达到请求上限时，工具结果已保存：通过")

        # 使用新的 Agent 和模型对象，模拟重新打开。
        reopened = SessionStore(path)
        resumed_model = FakeModel()

        resumed_agent = Agent(
            resumed_model,
            tools,
            context,
            session_store=reopened,
        )

        result = resumed_agent.resume()

        assert result["status"] == "completed"
        assert result["n_calls"] == 1
        assert resumed_model.calls == 1
        assert tools.executions == 1

        assert [
            message["role"]
            for message in result["messages"]
        ] == ["user", "assistant", "tool", "assistant"]

        print("恢复时不添加用户消息、不重复执行旧工具：通过")

        before = path.read_bytes()

        result = resumed_agent.resume()

        assert result["status"] == "completed"
        assert result["n_calls"] == 0
        assert resumed_model.calls == 1
        assert path.read_bytes() == before

        print("已完成的会话直接返回，不请求模型：通过")

        # 模拟调用记录已写入，但结果尚未保存。
        reopened.append_message({
            "role": "user",
            "content": "再读取一次",
        })

        reopened.append_message({
            "role": "assistant",
            "type": "tool_call",
            "id": "call_b",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        })

        before = path.read_bytes()

        result = resumed_agent.resume()

        assert result["status"] == "needs_recovery"
        assert result["pending_call_ids"] == ["call_b"]
        assert result["n_calls"] == 0

        assert resumed_model.calls == 1
        assert tools.executions == 1
        assert path.read_bytes() == before

        print("工具结果缺失时返回恢复状态，且不执行任何操作：通过")

        batch_store = SessionStore(
            Path(directory) / "batch-session.jsonl"
        )

        batch_store.create(SessionHeader(
            project_id="demo-project",
            owner_id="demo-user",
            cwd=directory,
        ))

        batch_store.append_message({
            "role": "user",
            "content": "读取两个文件",
        })

        batch_store.append_message({
            "role": "assistant",
            "type": "tool_batch",
            "calls": [
                {
                    "type": "tool_call",
                    "id": "batch_a",
                    "name": "read_file",
                    "arguments": {"path": "a.py"},
                },
                {
                    "type": "tool_call",
                    "id": "batch_b",
                    "name": "read_file",
                    "arguments": {"path": "b.py"},
                },
            ],
        })

        batch_store.append_message({
            "role": "tool",
            "tool_call_id": "batch_a",
            "content": "a.py 的内容",
            "is_error": False,
        })

        batch_model = FakeModel()
        batch_tools = FakeTools()

        batch_agent = Agent(
            batch_model,
            batch_tools,
            context,
            session_store=batch_store,
        )

        before = batch_store.path.read_bytes()
        result = batch_agent.resume()

        assert result["status"] == "needs_recovery"
        assert result["pending_call_ids"] == ["batch_b"]
        assert batch_model.calls == 0
        assert batch_tools.executions == 0
        assert batch_store.path.read_bytes() == before

        print("批量调用中断时，仅报告缺少结果的调用：通过")


if __name__ == "__main__":
    main()