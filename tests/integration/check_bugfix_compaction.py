from pathlib import Path
from tempfile import TemporaryDirectory

from tests.bugfix.check_bugfix_workspace import git
from tests.context.check_session_compactor import FakeSummaryModel

from forgepilot.bugfix_service import BugfixService
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import CompactionEntry, MessageEntry
from forgepilot.sessions.store import SessionStore


class CompactionCheckModel:
    max_output_tokens = 2048

    def __init__(self):
        self.business_calls = 0
        self.summary_model = FakeSummaryModel()
        self.saw_summary = False
        self.selection_calls = 0

    @staticmethod
    def is_summary_request(tools):
        return any(
            tool["name"] == "submit_context_summary"
            for tool in tools
        )

    @staticmethod
    def is_selection_request(tools):
        return any(
            tool["name"] == "submit_collapse_choice"
            for tool in tools
        )

    def estimate_input_tokens(self, *, messages, tools):
        if self.is_selection_request(tools):
            return 1000
        if self.is_summary_request(tools):
            return 1000

        # 第四次工具执行后，模拟业务上下文达到压缩阈值。
        if (
            self.business_calls >= 4
            and self.summary_model.calls == 0
        ):
            return 26000

        return 1000



    def generate(self, *, messages, tools):
        if self.is_selection_request(tools):
            self.selection_calls += 1

            return {
                "type": "tool_call",
                "id": f"selection_{self.selection_calls}",
                "name": "submit_collapse_choice",
                "arguments": {
                    "action": "skip",
                    "candidate_id": None,
                    "reason": "本次检查模拟没有适合折叠的片段。",
                },
            }
        if self.is_summary_request(tools):
            return self.summary_model.generate(
                messages=messages,
                tools=tools,
            )

        self.saw_summary |= any(
            message.get("role") == "user"
            and "历史对话的压缩摘要" in message.get("content", "")
            for message in messages
        )

        self.business_calls += 1

        if self.business_calls <= 6:
            number = self.business_calls

            return {
                "type": "tool_call",
                "id": f"call_{number}",
                "name": "run_command",
                "arguments": {
                    "command": (
                        f'python -c "print(\'evidence_{number}\')"'
                    ),
                },
            }

        assert self.business_calls == 7

        return {
            "type": "final",
            "content": "检查完成，未修改代码。",
        }


def main():
    with TemporaryDirectory(
        prefix="forgepilot-bugfix-compaction-"
    ) as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()

        (source / "README.md").write_text(
            "demo\n",
            encoding="utf-8",
        )

        git(source, "init")
        git(source, "add", ".")
        git(
            source,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "commit", "-m", "initial",
        )

        model = CompactionCheckModel()

        service = BugfixService(
            model=model,
            source=source,
            tasks_root=root / "tasks",
            budget=ContextBudget(
                context_window=32000,
                max_output_tokens=2048,
                safety_margin=2000,
            ),
        )

        result = service.run(
            "检查命令执行和历史压缩，不修改文件。"
        )

        assert result["status"] == "completed", result

        store = SessionStore(
            Path(result["run_dir"]) / "session.jsonl"
        )
        branch = store.get_current_branch()

        compactions = [
            entry
            for entry in branch
            if isinstance(entry, CompactionEntry)
        ]

        assert len(compactions) == 1
        assert model.summary_model.calls == 1
        assert model.saw_summary
        assert model.selection_calls == 1
        assert store.get_current_collapse_records() == []

        print("Collapse 跳过后，前缀压缩仍正常执行：通过")
        print("自动前缀压缩，后续模型收到摘要：通过")

        original_results = [
            entry.payload
            for entry in branch
            if isinstance(entry, MessageEntry)
            and entry.payload.get("role") == "tool"
        ]

        assert len(original_results) == 6
        assert "evidence_1" in original_results[0]["content"]

        print("压缩后原始工具记录仍然完整：通过")

        report = Path(
            result["delivery"]["report_path"]
        ).read_text(encoding="utf-8")

        for number in range(1, 7):
            assert f"### run_command / call_{number}" in report
            assert f"evidence_{number}" in report

        print("修复报告保留压缩前后的命令证据：通过")

        git(source, "diff", "--exit-code", "HEAD", "--")

        print("原工作区未修改：通过")


if __name__ == "__main__":
    main()
