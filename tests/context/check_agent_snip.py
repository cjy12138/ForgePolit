from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.artifacts.store import ArtifactStore
from forgepilot.contracts import ToolContext
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.artifacts import build_artifact_tools

from tests.context.check_agent_context_budget import RecordingModel


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        store = SessionStore(root / "session.jsonl")
        store.create(SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=directory,
        ))
        artifacts = ArtifactStore(root / "artifacts")

        store.append_message({
            "role": "user",
            "content": "分析工具输出。",
        })

        for index in range(3):
            text = f"第 {index} 组\n" + "完整输出\n" * 4000
            ref = artifacts.save_text(text)

            store.append_message({
                "role": "assistant",
                "type": "tool_call",
                "id": f"call_{index}",
                "name": "run_python",
                "arguments": {"args": ["demo.py"]},
            })
            store.append_message({
                "role": "tool",
                "tool_call_id": f"call_{index}",
                "tool_name": "run_python",
                "content": text,
                "is_error": False,
                "exit_code": 0,
                "artifact": ref.model_dump(),
            })

        before = store.path.read_bytes()

        registry = ToolRegistry(build_artifact_tools(
            session_store=store,
            artifact_store=artifacts,
        ))
        model = RecordingModel(estimated_tokens=200)

        agent = Agent(
            model,
            registry,
            ToolContext(root),
            session_store=store,
            artifact_store=artifacts,
            context_budget=ContextBudget(
                context_window=1000,
                max_output_tokens=100,
                safety_margin=100,
            ),
            enable_snip=True,
        )

        result = agent.resume()

        assert result["status"] == "completed"
        assert result["n_calls"] == 1
        assert model.estimated_requests == model.sent_requests

        sent = model.sent_requests[0]
        outputs = [
            message
            for message in sent["messages"]
            if message["role"] == "tool"
        ]

        assert outputs[0]["snipped"] is True
        assert not outputs[1].get("snipped", False)
        assert not outputs[2].get("snipped", False)
        print("实际请求使用 Snip 视图，最近两组保留原文：通过")

        assert any(
            tool["name"] == "read_artifact"
            for tool in sent["tools"]
        )
        print("预算估算与发送内容一致，且提供读回工具：通过")

        # 原有文件内容保留，只追加了模型最终回复。
        assert store.path.read_bytes().startswith(before)

        raw_outputs = [
            node.payload
            for node in store.get_current_branch()
            if node.type == "message"
            and node.payload.get("role") == "tool"
        ]
        assert all("snipped" not in item for item in raw_outputs)
        assert all(len(item["content"]) > 12000 for item in raw_outputs)
        print("磁盘历史仍保存完整工具正文：通过")

        # 已完成会话再次恢复，不请求模型。
        again = agent.resume()
        assert again["n_calls"] == 0
        assert len(model.sent_requests) == 1
        print("再次恢复不重复请求模型：通过")


if __name__ == "__main__":
    main()