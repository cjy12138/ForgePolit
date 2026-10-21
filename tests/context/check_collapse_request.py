from pathlib import Path
from tempfile import TemporaryDirectory

from tests.context.check_collapse_summary import FakeSummaryWorker
from forgepilot.sessions.collapse.planning import plan_collapse
from forgepilot.sessions.collapse.request_view import (
    build_collapsed_request,
)
from forgepilot.sessions.collapse.summarizer import (
    CollapseSummarizer,
    build_collapse_record,
)
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.context import build_request_messages
from forgepilot.sessions.models import (
    CompactionEntry,
    SessionHeader,
)
from forgepilot.sessions.store import SessionStore
from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.collapse.request_view import (
    build_layered_request,
)
from forgepilot.sessions.snip.planner import plan_snip_records

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

        user = store.append_message({
            "role": "user",
            "content": "检查项目，不修改测试。",
        })

        calls = []
        results = []
        artifacts = ArtifactStore(
            Path(directory) / "artifacts"
        )

        for index in range(4):
            call_id = f"call_{index}"

            calls.append(store.append_message({
                "role": "assistant",
                "type": "tool_call",
                "id": call_id,
                "name": "read_file",
                "arguments": {"path": "calculator.py"},
            }))

            content = (
                f"第 {index} 组输出"
                + ("x" * 10000 if index == 0 else "")
            )
            payload = {
                "role": "tool",
                "tool_call_id": call_id,
                "content": content,
                "is_error": False,
                "exit_code": 0,
            }
            if index == 0:
                payload["artifact"] = artifacts.save_text(
                    content
                ).model_dump()

            results.append(store.append_message(payload))

        branch = store.get_current_branch()

        candidate = CollapseSummarizer(
            FakeSummaryWorker()
        ).summarize(
            plan_collapse(
                branch,
                source_start_id=calls[1].id,
                source_end_id=results[1].id,
            )
        )

        record = build_collapse_record(
            candidate,
            session_id=header.session_id,
        )
        store.append_collapse(record)

        before = store.path.read_bytes()

        messages = build_collapsed_request(
            branch,
            store.get_current_collapse_records(),
        )

        # 第一组保留，第二组替换为一条摘要，最后两组保留。
        assert len(messages) == 8
        assert messages[:3] == [
            user.payload,
            calls[0].payload,
            results[0].payload,
        ]
        assert "局部片段的摘要" in messages[3]["content"]
        assert messages[4:] == [
            calls[2].payload,
            results[2].payload,
            calls[3].payload,
            results[3].payload,
        ]

        assert store.path.read_bytes() == before
        assert store.get_current_branch() == branch
        print("中间替换为摘要，两侧原文和历史不变：通过")
        for snip_record in plan_snip_records(branch, artifacts):
            store.append_snip(snip_record)

        saved = store.path.read_bytes()

        layered = build_layered_request(
            branch,
            artifacts,
            store.get_current_snip_records(),
            store.get_current_collapse_records(),
        )

        assert len(layered) == 8

        # 第一组工具输出被 Snip。
        assert layered[2].get("snipped") is True
        assert len(layered[2]["content"]) < len(
            results[0].payload["content"]
        )

        # 第二组显示局部摘要。
        assert "局部片段的摘要" in layered[3]["content"]

        # 最近两组原文保留。
        assert layered[4:] == [
            calls[2].payload,
            results[2].payload,
            calls[3].payload,
            results[3].payload,
        ]

        assert store.path.read_bytes() == saved
        assert store.get_current_branch() == branch
        print("Snip 与 Collapse 共存，不修改原始历史：通过")

        without_snip = build_layered_request(
            branch,
            artifacts,
            store.get_current_snip_records(),
            store.get_current_collapse_records(),
            enable_snip=False,
        )

        assert without_snip == messages
        print("关闭 Snip 后，Collapse 仍然生效：通过")
        # 前缀压缩覆盖前两组，保留最后两组。
        prefix_plan = plan_compaction(
            branch,
            keep_recent_groups=2,
        )
        assert prefix_plan is not None

        prefix_result = FakeSummaryWorker().summarize_with_sources(
            prefix_plan
        )

        store.append(CompactionEntry(
            session_id=header.session_id,
            parent_id=prefix_plan.source_leaf_id,
            source_leaf_id=prefix_plan.source_leaf_id,
            first_kept_entry_id=prefix_plan.first_kept_entry_id,
            summary=prefix_result.summary,
        ))

        updated_branch = store.get_current_branch()

        after_prefix = build_collapsed_request(
            updated_branch,
            store.get_current_collapse_records(),
        )

        assert after_prefix == build_request_messages(
            updated_branch
        )
        print("前缀摘要覆盖局部范围后，不重复发送：通过")


if __name__ == "__main__":
    main()
