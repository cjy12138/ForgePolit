from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.control.controller import ContextController

from tests.context.check_agent_context_budget import RecordingModel
from tests.context.check_session_compactor import (
    FakeSummaryModel,
    make_store,
)


def main():
    with TemporaryDirectory() as directory:
        store = make_store(directory, "circuit.jsonl")
        artifacts = ArtifactStore(Path(directory) / "artifacts")

        model = RecordingModel(estimated_tokens=750)
        summary_model = FakeSummaryModel(fail=True)

        controller = ContextController(
            model=model,
            tools=[],
            store=store,
            budget=ContextBudget(
                context_window=1000,
                max_output_tokens=100,
                safety_margin=100,
            ),
            artifacts=artifacts,
            compactor=SessionCompactor(
                store,
                __import__(
                    "forgepilot.sessions.summarization.summarizer",
                    fromlist=["ContextSummarizer"],
                ).ContextSummarizer(summary_model),
            ),
            enable_snip=False,
            keep_recent_groups=1,
            max_compaction_failures=3,
        )

        before = store.path.read_bytes()

        for expected_count in range(1, 4):
            try:
                controller.prepare()
            except ModelError as exc:
                assert exc.code == "fake_failure"
            else:
                raise AssertionError("本次作业应失败")

            assert (
                controller.consecutive_compaction_failures
                == expected_count
            )

        assert controller.compaction_circuit_open
        assert summary_model.calls == 3
        assert store.path.read_bytes() == before
        print("连续三次作业失败后打开熔断，原始历史保留：通过")

        # 750 超过触发点 720，但没有超过输入预算 800。
        prepared = controller.prepare()

        assert prepared.actions == ["compact_skipped_circuit_open"]
        assert summary_model.calls == 3
        print("熔断后预算允许时继续准备，不重复摘要调用：通过")

        model.estimated_tokens = 801

        try:
            controller.prepare()
        except ModelError as exc:
            assert exc.code == "compaction_circuit_open"
        else:
            raise AssertionError("熔断且超预算时应停止")

        assert summary_model.calls == 3
        assert model.sent_requests == []
        print("熔断后仍超预算时停止，不发送业务请求：通过")

        controller.reset_compaction_circuit()
        summary_model.fail = False
        model.estimated_tokens = 750

        prepared = controller.prepare()

        assert "compact" in prepared.actions
        assert summary_model.calls == 4
        assert controller.consecutive_compaction_failures == 0
        assert not controller.compaction_circuit_open
        print("显式重置后恢复，成功作业清零连续失败数：通过")


if __name__ == "__main__":
    main()