from dataclasses import replace

from forgepilot.sessions.collapse.records import CollapseRecord
from forgepilot.sessions.collapse.validation import (
    validate_collapse_record,
)
from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.models import SessionNode


def align_compaction_with_collapse(
    plan: CompactionPlan,
    branch: list[SessionNode],
    records: list[CollapseRecord],
) -> CompactionPlan | None:
    messages = [*plan.to_summarize, *plan.to_keep]

    positions = {
        node.id: index
        for index, node in enumerate(messages)
    }
    branch_ids = {node.id for node in branch}

    ranges = []

    for record in records:
        if record.source_leaf_id not in branch_ids:
            continue

        validate_collapse_record(record, branch)

        start = positions.get(record.source_start_id)
        end = positions.get(record.source_end_id)

        if start is None and end is None:
            # 已被更早的前缀摘要覆盖。
            continue

        if start is None or end is None:
            raise ValueError("已有前缀边界切开了 Collapse 范围")

        ranges.append((start, end))

    split = len(plan.to_summarize)

    # start < split <= end 表示保留起点落在范围内部。
    while True:
        moved = False

        for start, end in ranges:
            if start < split <= end:
                split = start
                moved = True
                break

        if not moved:
            break

    if split == 0:
        # 向前调整后，没有可总结的新原文。
        return None

    return replace(
        plan,
        to_summarize=messages[:split],
        to_keep=messages[split:],
    )