from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from tests.context.check_collapse_selector import FakeSelectionModel
from tests.context.check_controller_collapse import CountingCompactor
from tests.context.check_agent_auto_compaction import SequenceEstimateModel
from tests.context.check_session_collapser import SummaryModel
from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.budget.summary_job import SummaryJobLimits
from forgepilot.sessions.collapse.background import BackgroundCollapse
from forgepilot.sessions.collapse.collapser import SessionCollapser
from forgepilot.sessions.collapse.selector import CollapseSelector
from forgepilot.sessions.collapse.summarizer import CollapseSummarizer
from forgepilot.sessions.control.controller import ContextController
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.summarization.summarizer import ContextSummarizer


class GatedSummaryModel(SummaryModel):
    def __init__(self):
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def generate(self, messages, tools):
        self.entered.set()
        if not self.release.wait(5):
            raise AssertionError("测试未释放后台生成")
        return super().generate(messages, tools)


def main():
    for mode in ("publish", "stale", "urgent"):
        with TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "session.jsonl")
            store.create(SessionHeader(project_id="demo", owner_id="local", cwd=directory))
            root = store.append_message({"role": "user", "content": "检查项目。"})
            for index in range(6):
                cid = f"call_{index}"
                store.append_message({"role": "assistant", "type": "tool_call",
                                      "id": cid, "name": "read_file",
                                      "arguments": {"path": "calculator.py"}})
                store.append_message({"role": "tool", "tool_call_id": cid,
                                      "content": "x" * 12000, "is_error": False, "exit_code": 0})
            artifacts = ArtifactStore(Path(directory) / "artifacts")
            summary = GatedSummaryModel()
            sb = ContextBudget(context_window=32000, max_output_tokens=512, safety_margin=1000)
            collapser = SessionCollapser(
                store=store,
                selector=CollapseSelector(FakeSelectionModel("select"), context_budget=sb),
                summarizer=CollapseSummarizer(ChunkedSummarizer(
                    ContextSummarizer(summary, context_budget=sb))),
                model=summary, tools=[], artifacts=artifacts,
                job_limits=SummaryJobLimits(max_requests=9, max_reserved_tokens=200000),
            )
            background = BackgroundCollapse(collapser)
            try:
                compactor = CountingCompactor(store)
                business = SequenceEstimateModel([750] if mode == "urgent" else [650, 650, 500])
                controller = ContextController(
                    model=business, tools=[], store=store,
                    budget=ContextBudget(context_window=1000, max_output_tokens=100, safety_margin=100),
                    artifacts=artifacts, enable_snip=False, collapser=collapser,
                    background_collapse=background, compactor=compactor,
                )
                saved = store.path.read_bytes()
                first = controller.prepare()
                if mode == "urgent":
                    assert "collapse_started" not in first.actions
                    assert compactor.calls == 1
                    assert background._future is None
                    print("90% 压力下直接走前缀压缩：通过")
                    continue

                assert first.actions == ["collapse_started"]
                assert summary.entered.wait(2)
                assert store.path.read_bytes() == saved
                second = controller.prepare()
                assert second.actions == []
                assert controller.stats.collapse_attempts == 1
                assert store.path.read_bytes() == saved
                print("后台生成不阻塞下一次准备，不写会话、不重复启动：通过")

                summary.release.set()
                # Tests synchronize completion explicitly; Controller never waits on this future.
                assert background._future.result(timeout=5) is not None
                assert store.path.read_bytes() == saved
                if mode == "publish":
                    added = store.append_message({"role": "assistant", "type": "final", "content": "检查结束。"})
                    branch = store.get_current_branch()
                    third = controller.prepare()
                    assert "collapse" in third.actions
                    records = store.get_current_collapse_records()
                    assert len(records) == 1 and records[0].source_leaf_id == added.id
                    assert store.get_current_branch() == branch
                    assert compactor.calls == 0
                    assert any("局部片段的摘要" in m.get("content", "") for m in third.messages)
                    print("请求前提交候选，保留尾部消息与原始历史：通过")
                else:
                    store.move_cursor(root.id)
                    store.append_message({"role": "user", "content": "另一个任务。"})
                    saved = store.path.read_bytes()
                    third = controller.prepare()
                    assert "collapse_skipped" in third.actions
                    assert store.get_current_collapse_records() == []
                    assert store.path.read_bytes() == saved
                    print("来源已不在当前分支，丢弃后台候选：通过")
            finally:
                summary.release.set()
                background.close()


if __name__ == "__main__":
    main()
