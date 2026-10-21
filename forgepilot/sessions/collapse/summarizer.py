from dataclasses import dataclass
from typing import Protocol

from forgepilot.sessions.collapse.planning import (
    CollapsePlan,
    collapse_source_digest,
)
from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.summarization.artifacts import (
    validate_summary_result_sources,
)
from forgepilot.sessions.summarization.result import SummarizationResult
from forgepilot.sessions.summarization.schema import (
    validate_summary_sources,
)
from forgepilot.sessions.collapse.records import CollapseRecord
from forgepilot.sessions.budget.summary_job import SummaryJobBudget

class SummaryWorker(Protocol):
    def summarize_with_sources(
        self,
        plan: CompactionPlan,
        *,
        job_budget: SummaryJobBudget | None = None,
    ) -> SummarizationResult:
        ...

@dataclass(frozen=True)
class CollapseCandidate:
    plan: CollapsePlan
    result: SummarizationResult

class CollapseSummarizer:
    def __init__(self, worker: SummaryWorker):
        self.worker = worker

    def summarize(
            self,
            plan: CollapsePlan,
            *,
            job_budget: SummaryJobBudget | None = None,
    ) -> CollapseCandidate:
        if not plan.to_summarize:
            raise ValueError("没有需要折叠的消息")

        if collapse_source_digest(
                plan.to_summarize
        ) != plan.source_sha256:
            raise ValueError("折叠计划的来源已变化")

            # 给摘要器独立副本，避免它影响调用方持有的计划。
        copied_plan = CollapsePlan(
            source_leaf_id=plan.source_leaf_id,
            source_start_id=plan.source_start_id,
            source_end_id=plan.source_end_id,
            source_sha256=plan.source_sha256,
            to_summarize=[
                entry.model_copy(deep=True)
                for entry in plan.to_summarize
            ],
        )

        # 这里只适配现有摘要器的输入接口，
        # 不执行前缀压缩，也不发布 CompactionEntry。
        summary_plan = CompactionPlan(
            source_leaf_id=copied_plan.source_leaf_id,
            to_summarize=[
                entry.model_copy(deep=True)
                for entry in copied_plan.to_summarize
            ],
            to_keep=[],
            previous_summary=None,
            previous_compaction_id=None,
            previous_artifact_sources=[],
        )

        if job_budget is None:
            result = self.worker.summarize_with_sources(
                summary_plan
            )
        else:
            result = self.worker.summarize_with_sources(
                summary_plan,
                job_budget=job_budget,
            )

        # 局部摘要只能引用本次选中的原始消息。
        validate_summary_sources(
            result.summary,
            copied_plan.to_summarize,
        )
        # 校验完整工具输出的来源信息，防止遗漏或串入其他来源。
        validation_plan = CompactionPlan(
            source_leaf_id=copied_plan.source_leaf_id,
            to_summarize=copied_plan.to_summarize,
            to_keep=[],
        )
        validate_summary_result_sources(
            validation_plan,
            result,
        )

        return CollapseCandidate(
            plan=copied_plan,
            result=SummarizationResult(
                summary=result.summary.model_copy(deep=True),
                artifact_sources=[
                    source.model_copy(deep=True)
                    for source in result.artifact_sources
                ],
            ),
        )


def build_collapse_record(
    candidate: CollapseCandidate,
    *,
    session_id: str,
) -> CollapseRecord:
    plan = candidate.plan
    result = candidate.result

    # frozen dataclass 的内部列表仍可以被修改，
    # 因此转换成记录前重新检查来源。
    if collapse_source_digest(
        plan.to_summarize
    ) != plan.source_sha256:
        raise ValueError("折叠候选的来源已变化")

    validate_summary_sources(
        result.summary,
        plan.to_summarize,
    )

    validation_plan = CompactionPlan(
        source_leaf_id=plan.source_leaf_id,
        to_summarize=plan.to_summarize,
        to_keep=[],
    )

    validate_summary_result_sources(
        validation_plan,
        result,
    )

    return CollapseRecord(
        session_id=session_id,
        source_leaf_id=plan.source_leaf_id,
        source_start_id=plan.source_start_id,
        source_end_id=plan.source_end_id,
        source_sha256=plan.source_sha256,
        summary=result.summary.model_copy(deep=True),
        artifact_sources=[
            source.model_copy(deep=True)
            for source in result.artifact_sources
        ],
    )