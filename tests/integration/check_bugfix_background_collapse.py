"""通过 Event 控制后台时序，检查 bugfix 服务；不调用模型 API。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
from unittest.mock import patch

from tests.integration.check_bugfix_collapse import CollapseCheckModel
from tests.bugfix.check_bugfix_workspace import git
from forgepilot.bugfix_service import BugfixService
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.collapse.background import BackgroundCollapse
from forgepilot.sessions.models import CompactionEntry, MessageEntry
from forgepilot.sessions.store import SessionStore


class GatedCollapseModel(CollapseCheckModel):
    def __init__(self):
        super().__init__()
        self.entered = Event()
        self.release = Event()
        self.request_threads = []

    def estimate_input_tokens(self, *, messages, tools):
        if self.is_selection_request(tools) or self.is_summary_request(tools):
            return 1000
        return 1000 if self.has_collapse_summary(messages) else 23000

    def generate(self, *, messages, tools):
        assert self.is_selection_request(tools) or self.is_summary_request(tools)
        self.request_threads.append(get_ident())
        if self.is_summary_request(tools):
            self.entered.set()
            assert self.release.wait(10), "主流程没有释放后台摘要"
        return super().generate(messages=messages, tools=tools)


class BusinessModel(CollapseCheckModel):
    def __init__(self, collapse_model, workers):
        super().__init__()
        self.collapse_model = collapse_model
        self.workers = workers
        self.continued_while_pending = False
        self.checked_unpublished_candidate = False

    def generate(self, *, messages, tools):
        # 业务模型不能收到后台范围选择或摘要请求。
        assert not self.is_selection_request(tools)
        assert not self.is_summary_request(tools)
        assert get_ident() == self.workers[0].owner_thread
        worker = self.workers[0]

        if self.business_calls == 4:
            assert self.collapse_model.entered.wait(5), "后台摘要没有启动"
            assert not self.collapse_model.release.is_set()
            assert not worker._future.done()
            assert worker.store.get_current_collapse_records() == []
            self.continued_while_pending = True

        elif self.business_calls == 5:
            # 第五次命令已执行，后台仍在等待。检查尾部新消息已落盘。
            branch = worker.store.get_current_branch()
            assert any(
                isinstance(entry, MessageEntry)
                and entry.payload.get("tool_call_id") == "call_5"
                for entry in branch
            )
            saved = worker.store.path.read_bytes()
            self.collapse_model.release.set()
            # 仅测试显式等待，用来稳定控制时序；生产控制器不这样等待。
            assert worker._future.result(timeout=5) is not None
            assert worker.store.path.read_bytes() == saved
            assert worker.store.get_current_collapse_records() == []
            self.checked_unpublished_candidate = True

        elif self.business_calls == 6:
            # 第六次命令之后的新请求，主线程已经发布候选。
            records = worker.store.get_current_collapse_records()
            assert len(records) == 1
            assert self.has_collapse_summary(messages)
            assert worker.publish_threads == [get_ident()]

        return super().generate(messages=messages, tools=tools)


def main():
    workers = []

    class ObservedBackgroundCollapse(BackgroundCollapse):
        def __init__(self, collapser):
            super().__init__(collapser)
            self.owner_thread = get_ident()
            self.publish_threads = []
            publish = collapser.publish_candidate

            def observed_publish(*args, **kwargs):
                self.publish_threads.append(get_ident())
                return publish(*args, **kwargs)

            collapser.publish_candidate = observed_publish
            workers.append(self)

    with TemporaryDirectory(prefix="forgepilot-bugfix-background-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        (source / "README.md").write_text("demo\n", encoding="utf-8")
        git(source, "init")
        git(source, "add", ".")
        git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")

        collapse_model = GatedCollapseModel()
        model = BusinessModel(collapse_model, workers)
        try:
            with patch("forgepilot.bugfix_service.BackgroundCollapse", ObservedBackgroundCollapse):
                result = BugfixService(
                    model=model,
                    collapse_model=collapse_model,
                    source=source,
                    tasks_root=root / "tasks",
                    budget=ContextBudget(context_window=32000, max_output_tokens=2048, safety_margin=2000),
                ).run("检查后台折叠，不修改文件。")
        finally:
            collapse_model.release.set()

        assert result["status"] == "completed", result
        assert model.continued_while_pending
        assert model.checked_unpublished_candidate
        assert model.saw_collapse
        assert collapse_model.selection_calls == 1
        assert collapse_model.summary_model.calls == 1
        assert all(thread != get_ident() for thread in collapse_model.request_threads)
        assert model.summary_model.calls == 0
        assert workers[0]._closed and workers[0]._future is None
        print("后台使用独立模型，摘要等待期间主 Agent 仍执行命令：通过")
        print("候选生成不写会话，下一轮由主线程校验并发布：通过")
        print("任务结束关闭后台工作器：通过")

        store = SessionStore(Path(result["run_dir"]) / "session.jsonl")
        branch = store.get_current_branch()
        assert len(store.get_current_collapse_records()) == 1
        assert not any(isinstance(entry, CompactionEntry) for entry in branch)
        outputs = [
            entry.payload for entry in branch
            if isinstance(entry, MessageEntry) and entry.payload.get("role") == "tool"
        ]
        assert len(outputs) == 6
        report = Path(result["delivery"]["report_path"]).read_text(encoding="utf-8")
        for number, output in enumerate(outputs, start=1):
            assert f"evidence_{number}" in output["content"]
            assert f"### run_command / call_{number}" in report
            assert f"evidence_{number}" in report
        git(source, "diff", "--exit-code", "HEAD", "--")
        print("原始记录和报告完整保留，原工作区未修改：通过")


if __name__ == "__main__":
    main()
