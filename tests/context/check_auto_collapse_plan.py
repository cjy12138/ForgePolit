from pathlib import Path
from tempfile import TemporaryDirectory

from tests.context.check_collapse_summary import FakeSummaryWorker
from forgepilot.sessions.collapse.auto_planning import (
    plan_auto_collapse,
)
from forgepilot.sessions.collapse.summarizer import (
    CollapseSummarizer,
    build_collapse_record,
)
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


def main():
    with TemporaryDirectory() as directory:
        store = SessionStore(
            Path(directory) / "session.jsonl"
        )

        header = SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=directory,
        )
        store.create(header)

        store.append_message({
            "role": "user",
            "content": "检查项目，不修改测试。",
        })

        calls = []
        results = []

        for index in range(6):
            call_id = f"call_{index}"

            calls.append(store.append_message({
                "role": "assistant",
                "type": "tool_call",
                "id": call_id,
                "name": "read_file",
                "arguments": {"path": "calculator.py"},
            }))

            results.append(store.append_message({
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"第 {index} 组输出",
                "is_error": False,
                "exit_code": 0,
            }))

        branch = store.get_current_branch()
        summarizer = CollapseSummarizer(FakeSummaryWorker())

        first = plan_auto_collapse(
            branch,
            [],
            max_tool_groups=2,
        )

        assert first is not None
        assert first.source_start_id == calls[0].id
        assert first.source_end_id == results[1].id

        store.append_collapse(build_collapse_record(
            summarizer.summarize(first),
            session_id=header.session_id,
        ))
        print("自动选择较早的连续完整工具组：通过")

        before = store.path.read_bytes()

        second = plan_auto_collapse(
            branch,
            store.get_current_collapse_records(),
            max_tool_groups=2,
        )

        assert second is not None
        assert second.source_start_id == calls[2].id
        assert second.source_end_id == results[3].id

        first_ids = {
            node.id
            for node in first.to_summarize
        }
        second_ids = {
            node.id
            for node in second.to_summarize
        }

        assert not first_ids & second_ids
        assert store.path.read_bytes() == before
        print("跳过已有范围，规划不写入历史：通过")

        store.append_collapse(build_collapse_record(
            summarizer.summarize(second),
            session_id=header.session_id,
        ))

        third = plan_auto_collapse(
            branch,
            store.get_current_collapse_records(),
            max_tool_groups=2,
        )

        assert third is None
        print("只剩最近两组时，不再选择：通过")


if __name__ == "__main__":
    main()