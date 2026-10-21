"""脚本模型模拟折叠收益，检查 bugfix Collapse 接入；不调用 API。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.integration.check_bugfix_compaction import CompactionCheckModel
from tests.bugfix.check_bugfix_workspace import git
from forgepilot.bugfix_service import BugfixService
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import CompactionEntry, MessageEntry
from forgepilot.sessions.store import SessionStore


class CollapseCheckModel(CompactionCheckModel):
    def __init__(self):
        super().__init__()
        self.saw_collapse = False

    @staticmethod
    def has_collapse_summary(messages):
        return any(
            message.get("role") == "user"
            and message.get("content", "").startswith(
                "以下是历史对话中一个局部片段的摘要。"
            )
            for message in messages
        )

    def estimate_input_tokens(self, *, messages, tools):
        if (
            self.is_selection_request(tools)
            or self.is_summary_request(tools)
        ):
            return 1000

        # 根据请求视图确实出现局部摘要来模拟收益，而非只看生成次数。
        if self.has_collapse_summary(messages):
            return 1000

        if self.business_calls >= 4:
            return 23000

        return 1000

    def generate(self, *, messages, tools):
        if self.is_selection_request(tools):
            self.selection_calls += 1
            data = json.loads(messages[1]["content"])
            chosen = data["candidates"][0]

            return {
                "type": "tool_call",
                "id": f"selection_{self.selection_calls}",
                "name": "submit_collapse_choice",
                "arguments": {
                    "action": "collapse",
                    "candidate_id": chosen["candidate_id"],
                    "reason": "模拟选择一个已完成的合法片段。",
                },
            }

        if not self.is_summary_request(tools):
            self.saw_collapse |= self.has_collapse_summary(messages)

        return super().generate(messages=messages, tools=tools)


def main():
    with TemporaryDirectory(prefix="forgepilot-bugfix-collapse-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        (source / "README.md").write_text("demo\n", encoding="utf-8")

        git(source, "init")
        git(source, "add", ".")
        git(
            source,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "commit", "-m", "initial",
        )

        model = CollapseCheckModel()
        result = BugfixService(
            model=model,
            source=source,
            tasks_root=root / "tasks",
            budget=ContextBudget(
                context_window=32000,
                max_output_tokens=2048,
                safety_margin=2000,
            ),
        ).run("检查命令执行和局部折叠，不修改文件。")

        assert result["status"] == "completed", result
        store = SessionStore(Path(result["run_dir"]) / "session.jsonl")
        branch = store.get_current_branch()

        assert not any(isinstance(entry, CompactionEntry) for entry in branch)
        assert len(store.get_current_collapse_records()) == 1
        assert model.selection_calls == 1
        assert model.summary_model.calls == 1
        assert model.saw_collapse
        print("Collapse 成功发布，后续模型收到局部摘要：通过")
        print("折叠后低于阈值，无需前缀压缩：通过")

        original_results = [
            entry.payload
            for entry in branch
            if isinstance(entry, MessageEntry)
            and entry.payload.get("role") == "tool"
        ]
        assert len(original_results) == 6
        for number, message in enumerate(original_results, start=1):
            assert f"evidence_{number}" in message["content"]
        print("折叠后原始工具记录仍然完整：通过")

        report = Path(result["delivery"]["report_path"]).read_text(encoding="utf-8")
        for number in range(1, 7):
            assert f"### run_command / call_{number}" in report
            assert f"evidence_{number}" in report
        print("修复报告保留折叠前后的命令证据：通过")

        assert (source / "README.md").read_text(encoding="utf-8") == "demo\n"
        git(source, "diff", "--exit-code", "HEAD", "--")
        print("原工作区未修改：通过")


if __name__ == "__main__":
    main()
