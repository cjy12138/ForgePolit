import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.extraction import MemoryExtractionAgent
from forgepilot.memory.service import MemoryExtractionService
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


class FakeModel:
    max_output_tokens = 1024

    def __init__(self):
        self.calls = 0
        self.return_empty = False

    def estimate_input_tokens(self, *, messages, tools):
        return 100

    def generate(self, *, messages, tools):
        self.calls += 1

        data = json.loads(messages[1]["content"])

        memories = []

        if not self.return_empty:
            memories.append({
                "filename": "testing_preferences.md",
                "name": "测试偏好",
                "type": "feedback",
                "description": "用户要求优先使用 unittest。",
                "content": "编写 Python 测试时优先使用 unittest。",
                "source_entry_ids": [
                    data["focus_user_entry_id"]
                ],
            })

        return {
            "type": "tool_call",
            "id": f"submit_{self.calls}",
            "name": "submit_memory_proposal",
            "arguments": {
                "memories": memories,
            },
        }


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        sessions = SessionStore(root / "session.jsonl")
        sessions.create(SessionHeader(
            owner_id="local-user",
            project_id="demo",
            cwd=str(root),
        ))

        memory_store = MarkdownMemoryStore(root / "memory")
        model = FakeModel()

        agent = MemoryExtractionAgent(
            model=model,
            memory_store=memory_store,
            context_budget=ContextBudget(
                context_window=16000,
                max_output_tokens=1024,
                safety_margin=1000,
            ),
        )

        service = MemoryExtractionService(
            sessions=sessions,
            memory_store=memory_store,
            agent=agent,
        )

        sessions.append_message({
            "role": "user",
            "content": "以后优先使用 unittest。",
        })

        # 尚无最终回答，不应调用模型。
        result = service.process_completed_turn()

        assert result["status"] == "not_ready"
        assert model.calls == 0
        print("未完成轮次不触发提取：通过")

        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "好的。",
        })

        before = sessions.path.read_bytes()
        result = service.process_completed_turn()

        assert result["status"] == "published"
        assert result["filenames"] == [
            "testing_preferences.md"
        ]
        assert model.calls == 1

        saved = memory_store.read("testing_preferences.md")
        assert "unittest" in saved.content
        assert sessions.path.read_bytes() == before

        print("完成轮次提取并发布：通过")

        # 同一个服务再次处理，直接跳过。
        result = service.process_completed_turn()

        assert result["status"] == "already_processed"
        assert model.calls == 1

        # 新建服务对象，模拟恢复后重新读取状态。
        reopened = MemoryExtractionService(
            sessions=sessions,
            memory_store=memory_store,
            agent=agent,
        )

        result = reopened.process_completed_turn()

        assert result["status"] == "already_processed"
        assert model.calls == 1
        print("同轮重复调用与重新打开均跳过：通过")

        # 新的一轮没有可复用信息。
        sessions.append_message({
            "role": "user",
            "content": "谢谢。",
        })
        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "不客气。",
        })

        model.return_empty = True

        result = reopened.process_completed_turn()

        assert result["status"] == "no_memories"
        assert result["filenames"] == []
        assert model.calls == 2

        result = reopened.process_completed_turn()

        assert result["status"] == "already_processed"
        assert model.calls == 2

        state = json.loads(
            reopened.state_path.read_text(encoding="utf-8")
        )
        assert len(state["processed_final_ids"]) == 2

        print("无可保存记忆也记录成功并去重：通过")

        dream_state = json.loads(
            reopened.dream_state.path.read_text(encoding="utf-8")
        )

        assert dream_state["activity"] == {
            reopened.session_id: sessions.get_current_branch()[-1].id
        }

        print("记忆提取成功后登记最新会话活动：通过")


if __name__ == "__main__":
    main()