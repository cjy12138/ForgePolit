from pathlib import Path
from tempfile import TemporaryDirectory

from tests.context.check_agent_auto_compaction import SequenceEstimateModel
from tests.context.check_collapse_summary import FakeSummaryWorker

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.collapse.planning import plan_collapse
from forgepilot.sessions.collapse.summarizer import (
    CollapseSummarizer,
    build_collapse_record,
)
from forgepilot.sessions.control.controller import ContextController
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


class FakeCollapser:
    def __init__(self, store, mode):
        self.store = store
        self.mode = mode
        self.calls = 0

    def collapse(self, *, enable_snip):
        self.calls += 1

        if self.mode == "skip":
            return None

        if self.mode == "insufficient":
            raise ModelError(
                "collapse_insufficient_savings",
                "模拟收益不足。",
            )

        if self.mode == "invalid":
            raise ModelError(
                "invalid_collapse_selection",
                "模拟非法选择。",
            )

        branch = self.store.get_current_branch()

        # 用户消息后紧接第一组工具调用和结果。
        plan = plan_collapse(
            branch,
            source_start_id=branch[1].id,
            source_end_id=branch[2].id,
        )

        candidate = CollapseSummarizer(
            FakeSummaryWorker()
        ).summarize(plan)

        record = build_collapse_record(
            candidate,
            session_id=branch[-1].session_id,
        )

        assert self.store.append_collapse(record)
        return record


class CountingCompactor:
    def __init__(self, store):
        self.store = store
        self.calls = 0

    def compact(self, *, keep_recent_groups):
        self.calls += 1
        return None


def main():
    for mode in ("low", "success", "skip", "insufficient", "invalid"):
        with TemporaryDirectory() as directory:
            store = SessionStore(
                Path(directory) / "session.jsonl"
            )
            store.create(SessionHeader(
                project_id="demo",
                owner_id="local",
                cwd=directory,
            ))

            store.append_message({
                "role": "user",
                "content": "检查项目。",
            })

            for index in range(4):
                call_id = f"call_{index}"

                store.append_message({
                    "role": "assistant",
                    "type": "tool_call",
                    "id": call_id,
                    "name": "read_file",
                    "arguments": {"path": "calculator.py"},
                })
                store.append_message({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": f"输出 {index}",
                    "is_error": False,
                    "exit_code": 0,
                })

            budget = ContextBudget(
                context_window=1000,
                max_output_tokens=100,
                safety_margin=100,
            )
            # 输入预算 800；Collapse 640；前缀压缩 720。

            estimates = (
                [600] if mode == "low"
                else [650, 500] if mode == "success"
                else [750]
            )

            model = SequenceEstimateModel(estimates)
            collapser = FakeCollapser(store, mode)
            compactor = CountingCompactor(store)

            original = store.path.read_bytes()
            original_branch = store.get_current_branch()

            controller = ContextController(
                model=model,
                tools=[],
                store=store,
                budget=budget,
                artifacts=ArtifactStore(
                    Path(directory) / "artifacts"
                ),
                enable_snip=False,
                collapser=collapser,
                compactor=compactor,
            )

            try:
                prepared = controller.prepare()
            except ModelError as exc:
                assert mode == "invalid"
                assert exc.code == "invalid_collapse_selection"
                assert compactor.calls == 0
                assert store.path.read_bytes() == original
                print("非法选择不被当成普通跳过：通过")
                continue

            if mode == "low":
                assert collapser.calls == 0
                assert compactor.calls == 0
                assert prepared.actions == []
                print("未达到 80% 时，不启动 Collapse：通过")

            elif mode == "success":
                assert collapser.calls == 1
                assert compactor.calls == 0
                assert prepared.actions == ["collapse"]
                assert prepared.decision.input_tokens == 500
                assert controller.stats.collapse_successes == 1
                assert any(
                    "局部片段的摘要" in message.get("content", "")
                    for message in prepared.messages
                )
                print("Collapse 后重新估算，压力降低则停止：通过")

            else:
                assert collapser.calls == 1
                assert compactor.calls == 1
                assert controller.stats.collapse_skips == 1
                assert store.path.read_bytes() == original
                print(f"{mode}：继续判断前缀压缩：通过")

            assert store.get_current_branch() == original_branch


if __name__ == "__main__":
    main()