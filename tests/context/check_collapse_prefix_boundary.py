from pathlib import Path
from tempfile import TemporaryDirectory

from tests.context.check_collapse_summary import FakeSummaryWorker
from tests.context.check_token_compaction import WeightedModel

from forgepilot.sessions.collapse.planning import plan_collapse
from forgepilot.sessions.collapse.summarizer import (
    CollapseSummarizer,
    build_collapse_record,
)
from forgepilot.sessions.compaction.collapse_boundary import (
    align_compaction_with_collapse,
)
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.compaction.token_planning import (
    plan_compaction_by_tokens,
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
            "content": "检查项目。",
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
                "content": f"输出 {index}",
                "is_error": False,
                "exit_code": 0,
            }))

        branch = store.get_current_branch()

        # 折叠第 2～4 组，最后两组仍受保护。
        candidate = CollapseSummarizer(
            FakeSummaryWorker()
        ).summarize(plan_collapse(
            branch,
            source_start_id=calls[1].id,
            source_end_id=results[3].id,
        ))

        record = build_collapse_record(
            candidate,
            session_id=header.session_id,
        )
        store.append_collapse(record)

        original = store.path.read_bytes()

        plan = plan_compaction(
            branch,
            keep_recent_groups=4,
        )
        assert plan is not None

        # 原计划保留第 3 组开始，切入了折叠范围。
        assert plan.first_kept_entry_id == calls[2].id

        aligned = align_compaction_with_collapse(
            plan,
            branch,
            [record],
        )
        assert aligned is not None
        assert aligned.first_kept_entry_id == calls[1].id

        selected_ids = {
            node.id
            for node in candidate.plan.to_summarize
        }
        kept_ids = {
            node.id
            for node in aligned.to_keep
        }

        assert selected_ids <= kept_ids
        assert not selected_ids & {
            node.id
            for node in aligned.to_summarize
        }
        print("边界向前对齐，局部范围整体保留：通过")

        token_plan = plan_compaction_by_tokens(
            branch,
            model=WeightedModel(),
            tools=[],
            target_input_tokens=800,
            summary_reserve_tokens=100,
            collapse_records=[record],
        )

        assert token_plan is not None

        # 整体保留范围时预算不足，最终选择整体覆盖它。
        assert token_plan.first_kept_entry_id == calls[4].id
        assert selected_ids <= {
            node.id
            for node in token_plan.to_summarize
        }
        print("Token 规划继续寻找边界，局部范围整体覆盖：通过")

        assert store.path.read_bytes() == original
        assert store.get_current_branch() == branch
        print("边界规划不修改会话历史：通过")


if __name__ == "__main__":
    main()