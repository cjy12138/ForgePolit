import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.extraction import MemoryExtractionAgent
from forgepilot.memory.models import MemoryDocument, MemoryMetadata
from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.memory.validation import validate_memory_proposal
from forgepilot.memory.publication import publish_memory_updates
from forgepilot.memory.models import MemoryCandidate, MemoryProposal
from forgepilot.memory.publication import publish_memories

class FakeMemoryModel:
    max_output_tokens = 1024

    def __init__(self):
        self.calls = 0
        self.source_entry_id = None

    def estimate_input_tokens(self, *, messages, tools):
        return 100

    def generate(self, *, messages, tools):
        self.calls += 1

        if self.calls == 1:
            data = json.loads(messages[1]["content"])
            self.source_entry_id = data["focus_user_entry_id"]

            assert "testing_preferences.md" in (
                data["existing_memory_manifest"]
            )

            assert {tool["name"] for tool in tools} == {
                "glob_memory",
                "grep_memory",
                "read_memory",
                "list_memory_versions",
                "read_memory_version",
                "submit_memory_proposal",
            }

            return {
                "type": "tool_call",
                "id": "read_a",
                "name": "read_memory",
                "arguments": {
                    "filename": "testing_preferences.md",
                },
            }

        if self.calls == 2:
            observation = messages[-1]

            assert observation["role"] == "tool"
            assert observation["tool_call_id"] == "read_a"
            assert observation["is_error"] is False

            document = json.loads(observation["content"])
            assert "根据实际结果报告测试情况" in (
                document["content"]
            )

            return {
                "type": "tool_call",
                "id": "submit_a",
                "name": "submit_memory_proposal",
                "arguments": {
                    "memories": [{
                        "filename": "testing_preferences.md",
                        "name": "测试偏好",
                        "type": "feedback",
                        "description": (
                            "优先使用 unittest，按实际结果报告测试。"
                        ),
                        "content": (
                            "优先使用 unittest。"
                            "根据实际结果报告测试情况。"
                        ),
                        "source_entry_ids": [
                            self.source_entry_id,
                        ],
                    }],
                },
            }

        raise AssertionError("提交后不应继续请求")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        memory_store = MarkdownMemoryStore(root / "memory")
        memory_store.save(MemoryDocument(
            filename="testing_preferences.md",
            metadata=MemoryMetadata(
                name="测试偏好",
                type="feedback",
                description="按实际结果报告测试。",
                source_session_id="old-session",
                source_entry_ids=["old-entry"],
            ),
            content="根据实际结果报告测试情况。",
        ))

        sessions = SessionStore(root / "session.jsonl")
        sessions.create(SessionHeader(
            owner_id="local-user",
            project_id="demo",
            cwd=str(root),
        ))

        sessions.append_message({
            "role": "user",
            "content": "以后优先使用 unittest。",
        })
        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "好的。",
        })

        snapshot = build_memory_snapshot(sessions)

        session_before = sessions.path.read_bytes()
        memory_before = {
            path.name: path.read_bytes()
            for path in memory_store.root.glob("*.md")
        }

        model = FakeMemoryModel()
        agent = MemoryExtractionAgent(
            model=model,
            memory_store=memory_store,
            context_budget=ContextBudget(
                context_window=16000,
                max_output_tokens=1024,
                safety_margin=1000,
            ),
        )

        proposal = agent.extract(snapshot)
        proposal = validate_memory_proposal(
            proposal,
            snapshot=snapshot,
            sessions=sessions,
        )

        # 验证编造来源会被拒绝。
        invalid = proposal.model_copy(deep=True)
        invalid.memories[0].source_entry_ids = ["missing-entry"]

        try:
            validate_memory_proposal(
                invalid,
                snapshot=snapshot,
                sessions=sessions,
            )
        except ValueError as exc:
            assert "快照之外" in str(exc)
        else:
            raise AssertionError("应拒绝不存在的来源节点")

        print("候选来源校验：通过")

        assert model.calls == 2
        assert len(proposal.memories) == 1

        candidate = proposal.memories[0]
        assert candidate.filename == "testing_preferences.md"
        assert "unittest" in candidate.content
        assert "根据实际结果报告测试情况" in candidate.content
        assert candidate.source_entry_ids == [
            snapshot.user_entry_id
        ]

        assert sessions.path.read_bytes() == session_before
        assert {
            path.name: path.read_bytes()
            for path in memory_store.root.glob("*.md")
        } == memory_before

        print("读取已有主题后提交候选：通过")
        print("只提供记忆工具，提交后结束：通过")
        print("提取阶段不修改会话或正式记忆：通过")

        try:
            publish_memory_updates(
                proposal,
                snapshot=snapshot,
                sessions=sessions,
                memory_store=memory_store,
                read_versions={},
            )
        except ValueError as exc:
            assert "更新前必须读取" in str(exc)
        else:
            raise AssertionError("应拒绝未读取主题的更新")

        print("更新前必须读取已有主题：通过")

        documents = publish_memory_updates(
            proposal,
            snapshot=snapshot,
            sessions=sessions,
            memory_store=memory_store,
            read_versions=dict(agent.read_versions),
        )

        assert len(documents) == 1

        saved = memory_store.read("testing_preferences.md")

        assert "unittest" in saved.content
        assert "根据实际结果报告测试情况" in saved.content

        # 本轮新增信息的来源。
        assert saved.metadata.source_session_id == snapshot.session_id
        assert saved.metadata.source_entry_ids == [
            snapshot.user_entry_id
        ]

        # 旧信息的来源仍然保留。
        assert any(
            source.source_session_id == "old-session"
            and source.source_entry_ids == ["old-entry"]
            for source in saved.metadata.previous_sources
        )

        index = memory_store.read_index()
        assert index.count("(testing_preferences.md)") == 1
        assert saved.metadata.description in index

        # 更新后的 header 仍能被召回扫描发现。
        candidates, skipped = memory_store.scan_headers()

        assert any(
            item["filename"] == "testing_preferences.md"
            for item in candidates
        )
        assert not skipped

        assert sessions.path.read_bytes() == session_before

        print("更新正文、保留旧来源和维护索引：通过")

        # 重新提取，读取当前版本。
        fresh_agent = MemoryExtractionAgent(
            model=FakeMemoryModel(),
            memory_store=memory_store,
            context_budget=ContextBudget(
                context_window=16000,
                max_output_tokens=1024,
                safety_margin=1000,
            ),
        )

        fresh_proposal = fresh_agent.extract(snapshot)

        new_candidate = MemoryCandidate(
            filename="test_runner_reference.md",
            name="测试运行参考",
            type="reference",
            description="参考本轮用户要求选择测试框架。",
            content="用户要求优先使用 unittest。",
            source_entry_ids=[snapshot.user_entry_id],
        )

        mixed = MemoryProposal(
            memories=[
                new_candidate,
                *fresh_proposal.memories,
            ]
        )

        documents = publish_memories(
            mixed,
            snapshot=snapshot,
            sessions=sessions,
            memory_store=memory_store,
            read_versions=dict(fresh_agent.read_versions),
        )

        assert len(documents) == 2
        assert "test_runner_reference.md" in (
            memory_store.list_filenames()
        )
        assert "unittest" in memory_store.read(
            "testing_preferences.md"
        ).content

        print("同一批新增与更新：通过")


if __name__ == "__main__":
    main()
