import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.memory.dream_agent import AutoDreamAgent
from forgepilot.memory.dream_service import AutoDreamService
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.sessions.budget.context_budget import ContextBudget


class FakeDreamModel:
    max_output_tokens = 1024

    def __init__(self):
        self.calls = 0
        self.fail = False
        self.on_generate = None

    def estimate_input_tokens(self, *, messages, tools):
        return 100

    def generate(self, *, messages, tools):
        self.calls += 1

        if self.fail:
            raise ModelError(
                "fake_failure",
                "模拟整理模型请求失败。",
            )

        if self.on_generate is not None:
            self.on_generate()

        # 模拟检查正常完成，但没有需要修改的记忆。
        return {
            "type": "tool_call",
            "id": f"submit_{self.calls}",
            "name": "submit_dream_proposal",
            "arguments": {
                "rewrites": [],
                "deletes": [],
                "conflicts": [],
            },
        }


def main():
    with TemporaryDirectory() as directory:
        store = MarkdownMemoryStore(Path(directory))
        model = FakeDreamModel()

        agent = AutoDreamAgent(
            model=model,
            memory_store=store,
            context_budget=ContextBudget(
                context_window=16000,
                max_output_tokens=1024,
                safety_margin=1000,
            ),
        )

        service = AutoDreamService(
            memory_store=store,
            agent=agent,
        )

        start = 1_000_000.0
        one_day = 24 * 60 * 60
        due = start + one_day

        def read_state():
            return json.loads(
                service.state.path.read_text(encoding="utf-8")
            )

        result = service.run_if_due(now=start)

        assert result["status"] == "too_soon"
        assert model.calls == 0
        print("条件不足时不调用模型：通过")

        for index in range(5):
            service.state.record_activity(
                f"session_{index}",
                "final_1",
                now=start,
            )

        # 模型失败后，活动仍然保持待处理状态。
        model.fail = True
        result = service.run_if_due(now=due)

        assert result["status"] == "failed"
        assert model.calls == 1
        assert read_state()["last_success_at"] is None
        assert read_state()["processed"] == {}
        print("整理失败不更新成功时间和活动水位：通过")

        # 在模型处理期间，模拟已有会话新增回答和新会话出现。
        def add_activity_during_generation():
            service.state.record_activity(
                "session_0",
                "final_2",
                now=due,
            )
            service.state.record_activity(
                "session_new",
                "final_1",
                now=due,
            )

        model.fail = False
        model.on_generate = add_activity_during_generation

        result = service.run_if_due(now=due)

        assert result["status"] == "no_changes"
        assert result["new_sessions"] == 5
        assert model.calls == 2

        state = read_state()
        assert state["last_success_at"] == due
        assert len(state["processed"]) == 5
        print("失败后可重试，无需修改也记录正常完成：通过")

        assert state["processed"]["session_0"] == "final_1"
        assert state["activity"]["session_0"] == "final_2"

        assert "session_new" not in state["processed"]
        assert state["activity"]["session_new"] == "final_1"
        print("整理期间的新活动保留为待处理：通过")

        result = service.run_if_due(now=due + 1)

        assert result["status"] == "too_soon"
        assert model.calls == 2
        print("成功后不会立即重复调用模型：通过")

        # 再过一天，只有两个不同会话有新活动，仍不足五个。
        reopened = AutoDreamService(
            memory_store=store,
            agent=agent,
        )

        result = reopened.run_if_due(now=due + one_day)

        assert result == {
            "status": "not_enough_sessions",
            "sessions": 2,
        }
        assert model.calls == 2
        print("重新打开后按保存水位计算新活动：通过")


if __name__ == "__main__":
    main()