from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.control.controller import ContextController
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.tools.artifacts import build_artifact_tools
from forgepilot.tool_registry import ToolRegistry

from tests.context.check_agent_auto_compaction import SequenceEstimateModel


class UnexpectedCompactor:
    def __init__(self, store):
        self.store = store
        self.calls = 0

    def compact(self, **kwargs):
        self.calls += 1
        raise AssertionError("Snip 后压力已降低，不应该生成摘要")


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
            "content": "检查输出。",
        })

        for index in range(3):
            text = "完整输出\n" * 4000
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

        definitions = ToolRegistry(build_artifact_tools(
            session_store=store,
            artifact_store=artifacts,
        )).definitions()

        budget = ContextBudget(
            context_window=1000,
            max_output_tokens=100,
            safety_margin=100,
        )
        # 输入预算 800；Snip 阈值 560；摘要阈值 720。

        before = store.replay()
        compactor = UnexpectedCompactor(store)

        low_model = SequenceEstimateModel([500])
        low = ContextController(
            model=low_model,
            tools=definitions,
            store=store,
            budget=budget,
            artifacts=artifacts,
            compactor=compactor,
        ).prepare()

        assert low.actions == []
        assert not any(message.get("snipped") for message in low.messages)
        assert len(low_model.estimated_requests) == 1
        print("低压力时保留完整视图，不生成摘要：通过")

        model = SequenceEstimateModel([750, 500])
        prepared = ContextController(
            model=model,
            tools=definitions,
            store=store,
            budget=budget,
            artifacts=artifacts,
            compactor=compactor,
        ).prepare()

        assert prepared.actions == ["snip"]
        assert prepared.decision.input_tokens == 500
        assert compactor.calls == 0
        assert prepared.messages == model.estimated_requests[-1]["messages"]
        print("先 Snip，压力降低后停止，不生成摘要：通过")

        assert model.sent_requests == []
        assert store.replay() == before
        assert len(store.get_current_snip_records()) == 1
        print("准备阶段只增加控制记录，消息树和游标不变：通过")

        saved = store.path.read_bytes()
        reopened = SessionStore(store.path)

        restored_model = SequenceEstimateModel([500])
        restored = ContextController(
            model=restored_model,
            tools=definitions,
            store=reopened,
            budget=budget,
            artifacts=artifacts,
        ).prepare()

        assert restored.messages == prepared.messages
        assert restored.actions == ["snip_restored"]
        assert restored_model.sent_requests == []
        assert reopened.path.read_bytes() == saved

        print("低压力时恢复已有 Snip，不重复写记录：通过")


if __name__ == "__main__":
    main()