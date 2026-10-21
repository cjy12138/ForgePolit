from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.artifacts.store import ArtifactRef, ArtifactStore
from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import pending_tool_calls


class OneCallModel:
    def generate(self, messages, tools):
        return {
            "type": "tool_call",
            "id": "call_output",
            "name": "large_output",
            "arguments": {},
        }


class LargeOutputTools:
    def __init__(self):
        self.executions = 0
        self.output = "完整输出\n" * 5000 + "末尾标记"

    def definitions(self):
        return []

    def execute(self, name, arguments, *, context):
        self.executions += 1
        return ToolResult(
            ok=True,
            output=self.output,
            exit_code=0,
        )


class FailingArtifactStore(ArtifactStore):
    def save_text(self, text):
        raise OSError("模拟磁盘写入失败")


def make_store(directory, name):
    store = SessionStore(Path(directory) / f"{name}.jsonl")
    store.create(SessionHeader(
        project_id="demo",
        owner_id="local",
        cwd=directory,
    ))
    return store


def main():
    with TemporaryDirectory() as directory:
        context = ToolContext(Path(directory))

        store = make_store(directory, "success")
        tools = LargeOutputTools()
        agent = Agent(
            OneCallModel(),
            tools,
            context,
            max_steps=1,
            session_store=store,
        )

        result = agent.run("生成长输出")
        assert result["status"] == "step_limit"
        assert tools.executions == 1

        reopened = SessionStore(store.path)
        payload = reopened.get_current_branch()[-1].payload

        assert payload["content"] == tools.output
        assert payload["tool_name"] == "large_output"
        assert payload["tool_call_id"] == "call_output"
        assert payload["is_error"] is False
        assert payload["exit_code"] == 0

        ref = ArtifactRef.model_validate(payload["artifact"])
        artifacts = ArtifactStore(
            store.path.parent / "artifacts" / store.path.stem
        )

        assert artifacts.read_text(ref) == tools.output
        assert artifacts.read_text(ref).endswith("末尾标记")
        print("长输出完整保存，引用随会话持久化：通过")

        failed_store = make_store(directory, "failed")
        failed_tools = LargeOutputTools()

        failed_agent = Agent(
            OneCallModel(),
            failed_tools,
            context,
            max_steps=1,
            session_store=failed_store,
            artifact_store=FailingArtifactStore(
                Path(directory) / "broken-artifacts"
            ),
        )

        try:
            failed_agent.run("生成长输出")
        except OSError:
            pass
        else:
            raise AssertionError("产物保存失败应停止执行")

        assert failed_tools.executions == 1
        assert pending_tool_calls(
            failed_store.get_current_branch()
        ) == {"call_output"}

        recovered = failed_agent.resume()

        assert recovered["status"] == "needs_recovery"
        assert failed_tools.executions == 1
        print("保存失败后保留未知状态，不自动重执行工具：通过")


if __name__ == "__main__":
    main()