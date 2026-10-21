from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.control.outcomes import COMPACTION_STOP_CODES
from forgepilot.sessions.control.controller import ContextController

from tests.context.check_agent_context_budget import RecordingModel
from tests.context.check_session_compactor import make_store


class StoppingCompactor:
    def __init__(self, store, code):
        self.store = store
        self.code = code
        self.calls = 0

    def compact(self, **kwargs):
        self.calls += 1
        raise ModelError(self.code, "模拟压缩作业停止")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        for index, code in enumerate(sorted(COMPACTION_STOP_CODES)):
            store = make_store(directory, f"stop_{index}.jsonl")
            before = store.path.read_bytes()
            model = RecordingModel(estimated_tokens=750)
            compactor = StoppingCompactor(store, code)

            controller = ContextController(
                model=model,
                tools=[],
                store=store,
                budget=ContextBudget(
                    context_window=1000,
                    max_output_tokens=100,
                    safety_margin=100,
                ),
                artifacts=ArtifactStore(root / f"artifacts_{index}"),
                compactor=compactor,
                enable_snip=False,
                max_compaction_failures=3,
            )

            # 模拟已有两次真实失败。
            controller.set_compaction_failures(2)

            for _ in range(3):
                try:
                    controller.prepare()
                except ModelError as exc:
                    assert exc.code == code
                else:
                    raise AssertionError("应报告停止原因")

            assert controller.stats.compaction_attempts == 3
            assert controller.stats.compaction_stops == 3
            assert controller.stats.compaction_failures == 0
            assert controller.consecutive_compaction_failures == 2
            assert not controller.compaction_circuit_open
            assert store.path.read_bytes() == before
            assert model.sent_requests == []

            # 下一次是真实摘要错误，仍然推进失败熔断。
            compactor.code = "invalid_context_summary"

            try:
                controller.prepare()
            except ModelError as exc:
                assert exc.code == "invalid_context_summary"
            else:
                raise AssertionError("应报告摘要失败")

            assert controller.stats.compaction_failures == 1
            assert controller.stats.compaction_stops == 3
            assert controller.consecutive_compaction_failures == 3
            assert controller.compaction_circuit_open

        print("主动停止不增加或清零失败计数：通过")
        print("主动停止保留历史，不发送业务请求：通过")
        print("真实摘要错误仍推进失败熔断：通过")


if __name__ == "__main__":
    main()