from dataclasses import replace

from forgepilot.exceptions import ModelError
from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.grouping import group_entries
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.summarization.summarizer import ContextSummarizer


def select_summary_chunk(
    plan: CompactionPlan,
    summarizer: ContextSummarizer,
) -> list[MessageEntry]:
    budget = summarizer.context_budget

    if budget is None:
        raise ValueError("分块选择需要摘要预算")

    if not plan.to_summarize:
        raise ValueError("没有需要分块的历史")

    groups = group_entries(plan.to_summarize)
    selected: list[MessageEntry] = []

    for group in groups:
        candidate = [*selected, *group]

        candidate_plan = replace(
            plan,
            to_summarize=candidate,
        )

        messages, tools = summarizer.build_request(candidate_plan)

        estimated = summarizer.model.estimate_input_tokens(
            messages=messages,
            tools=tools,
        )

        decision = budget.assess(estimated)

        if decision.exceeds_budget:
            if not selected:
                raise ModelError(
                    "summary_group_too_large",
                    (
                        "摘要规则、旧摘要和第一个完整交互组"
                        "合计已超过预算，无法按完整组分块。"
                    ),
                )

            break

        selected = candidate

    return selected