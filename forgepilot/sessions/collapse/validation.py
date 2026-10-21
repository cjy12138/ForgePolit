from forgepilot.sessions.collapse.planning import plan_collapse
from forgepilot.sessions.collapse.records import CollapseRecord
from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.models import SessionNode
from forgepilot.sessions.summarization.artifacts import (
    validate_summary_result_sources,
)
from forgepilot.sessions.summarization.result import SummarizationResult
from forgepilot.sessions.summarization.schema import (
    validate_summary_sources,
)


def validate_collapse_record(
    record: CollapseRecord,
    branch: list[SessionNode],
) -> None:
    positions = {
        node.id: index
        for index, node in enumerate(branch)
    }

    if record.source_leaf_id not in positions:
        raise ValueError("Collapse 来源锚点不在当前分支中")

    if record.prepared_leaf_id is not None:
        if record.prepared_leaf_id not in positions:
            raise ValueError("Collapse 准备锚点不在当前分支中")

        if (
            positions[record.prepared_leaf_id]
            > positions[record.source_leaf_id]
        ):
            raise ValueError("Collapse 准备锚点不能晚于发布锚点")

    # 按生成时的历史检查，不让后来新增的消息改变来源判断。
    source_branch = branch[
        :positions[record.source_leaf_id] + 1
    ]

    plan = plan_collapse(
        source_branch,
        source_start_id=record.source_start_id,
        source_end_id=record.source_end_id,
    )

    if plan.source_sha256 != record.source_sha256:
        raise ValueError("Collapse 来源哈希与原始历史不一致")

    validate_summary_sources(
        record.summary,
        plan.to_summarize,
    )

    validate_summary_result_sources(
        CompactionPlan(
            source_leaf_id=plan.source_leaf_id,
            to_summarize=plan.to_summarize,
            to_keep=[],
        ),
        SummarizationResult(
            summary=record.summary,
            artifact_sources=record.artifact_sources,
        ),
    )