from forgepilot.sessions.collapse.planning import (
    CollapsePlan,
    collapse_source_digest,
)
from forgepilot.sessions.collapse.summarizer import (
    CollapseSummarizer,
    build_collapse_record,
)
from forgepilot.sessions.collapse.records import CollapseRecord
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.summarization.result import SummarizationResult
from forgepilot.sessions.summarization.schema import ContextSummary


class FakeSummaryWorker:
    def __init__(self):
        self.received_plan = None

    def summarize_with_sources(self, plan):
        self.received_plan = plan

        fields = {
            name: []
            for name in ContextSummary.model_fields
        }
        fields["completed"] = [{
            "text": "读取了 calculator.py。",
            "source_entry_ids": [
                plan.to_summarize[-1].id,
            ],
        }]

        return SummarizationResult(
            summary=ContextSummary.model_validate(fields),
            artifact_sources=[],
        )


def main():
    call = MessageEntry(
        session_id="demo",
        payload={
            "role": "assistant",
            "type": "tool_call",
            "id": "call_read",
            "name": "read_file",
            "arguments": {"path": "calculator.py"},
        },
    )

    result = MessageEntry(
        session_id="demo",
        parent_id=call.id,
        payload={
            "role": "tool",
            "tool_call_id": "call_read",
            "content": "def add(a, b):\n    return a + b\n",
            "is_error": False,
            "exit_code": 0,
        },
    )

    selected = [call, result]

    plan = CollapsePlan(
        source_leaf_id="later_leaf",
        source_start_id=call.id,
        source_end_id=result.id,
        source_sha256=collapse_source_digest(selected),
        to_summarize=selected,
    )

    before = [
        entry.model_dump(mode="json")
        for entry in selected
    ]

    worker = FakeSummaryWorker()
    candidate = CollapseSummarizer(worker).summarize(plan)

    received = worker.received_plan
    assert received is not None
    assert [
        entry.id
        for entry in received.to_summarize
    ] == [call.id, result.id]

    assert received.previous_summary is None
    assert received.previous_artifact_sources == []
    assert received.to_keep == []
    print("只总结选中片段，不混入前缀摘要：通过")

    assert candidate.result.summary.completed[0].source_entry_ids == [
        result.id
    ]
    assert candidate.plan.source_sha256 == plan.source_sha256

    assert [
        entry.model_dump(mode="json")
        for entry in selected
    ] == before
    print("生成候选，保留原始历史：通过")
    record = build_collapse_record(
        candidate,
        session_id="demo",
    )

    assert record.source_start_id == call.id
    assert record.source_end_id == result.id
    assert record.source_leaf_id == "later_leaf"
    assert record.source_sha256 == plan.source_sha256

    # 模拟写入 JSONL 后重新读取一条记录。
    restored = CollapseRecord.model_validate_json(
        record.model_dump_json()
    )

    assert restored == record
    print("折叠记录保存范围，支持 JSON 往返：通过")

    # 记录持有摘要副本，修改候选不影响记录。
    original_text = record.summary.completed[0].text
    candidate.result.summary.completed[0].text = "修改候选"

    assert record.summary.completed[0].text == original_text
    print("记录与候选不共享可变摘要：通过")


if __name__ == "__main__":
    main()