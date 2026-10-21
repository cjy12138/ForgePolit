import json
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.artifacts.store import ArtifactStore
from forgepilot.contracts import ToolContext
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.control.controller import ContextController
from forgepilot.sessions.files.budget import add_current_file_context
from forgepilot.sessions.models import (
    CompactionEntry,
    MessageEntry,
    SessionHeader,
)
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.schema import ContextSummary

from tests.context.check_agent_context_budget import NoTools, RecordingModel


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        context = ToolContext(root)
        store = SessionStore(root / "session.jsonl")
        store.create(SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=directory,
        ))

        store.append_message({
            "role": "user",
            "content": "检查 a.py。",
        })
        store.append_message({
            "role": "assistant",
            "type": "tool_call",
            "id": "read_a",
            "name": "read_file",
            "arguments": {"path": "a.py"},
        })
        store.append_message({
            "role": "tool",
            "tool_call_id": "read_a",
            "content": "历史旧内容",
            "is_error": False,
        })
        kept = store.append_message({
            "role": "user",
            "content": "继续分析。",
        })

        header, _ = store.read()
        store.append(CompactionEntry(
            session_id=header.session_id,
            parent_id=kept.id,
            source_leaf_id=kept.id,
            first_kept_entry_id=kept.id,
            summary=ContextSummary(**{
                name: []
                for name in ContextSummary.model_fields
            }),
        ))

        (root / "a.py").write_text("当前内容", encoding="utf-8")
        before = store.path.read_bytes()

        model = RecordingModel(estimated_tokens=200)
        tools = NoTools()
        artifacts = ArtifactStore(root / "artifacts")
        budget = ContextBudget(
            context_window=1000,
            max_output_tokens=100,
            safety_margin=100,
        )

        controller = ContextController(
            model=model,
            tools=tools.definitions(),
            store=store,
            budget=budget,
            artifacts=artifacts,
            enable_snip=False,
            file_restorer=partial(
                add_current_file_context,
                model=model,
                tools=tools.definitions(),
                context=context,
                authorize_read=lambda target: True,
                input_limit_tokens=budget.input_budget,
            ),
        )

        agent = Agent(
            model,
            tools,
            context,
            session_store=store,
            artifact_store=artifacts,
            context_controller=controller,
        )
        result = agent.resume()

        assert result["status"] == "completed"
        assert "file_restore" in agent.last_context_actions

        sent = model.sent_requests[0]
        supplemental = sent["messages"][-1]
        data = json.loads(supplemental["content"])

        assert data["type"] == "current_file_context"
        assert data["files"][0]["content"] == "当前内容"
        assert sent == model.estimated_requests[-1]
        print("恢复请求使用当前文件，最终估算与发送一致：通过")

        assert store.path.read_bytes().startswith(before)
        assert all(
            node.payload.get("content") != supplemental["content"]
            for node in store.get_current_branch()
            if isinstance(node, MessageEntry)
        )
        print("临时文件数据进入请求，不写入会话历史：通过")

        # 已完成会话直接返回，不重复恢复或请求模型。
        again = agent.resume()
        assert again["n_calls"] == 0
        assert len(model.sent_requests) == 1
        print("已完成会话恢复不增加模型调用：通过")


if __name__ == "__main__":
    main()