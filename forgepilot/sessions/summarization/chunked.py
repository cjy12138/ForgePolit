from copy import deepcopy
from dataclasses import replace

from forgepilot.exceptions import ModelError
from forgepilot.sessions.summarization.chunking import select_summary_chunk
from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.summarization.schema import ContextSummary
from forgepilot.sessions.summarization.result import SummarizationResult
from forgepilot.sessions.summarization.artifacts import (
    SummaryArtifactSource,
    merge_summary_artifacts,
)
from forgepilot.sessions.budget.summary_job import (
    SummaryJobBudget,
    SummaryJobLimits,
    check_summary_cancelled,
)
from collections.abc import Callable

class ChunkedSummarizer:
    def __init__(
        self,
        summarizer: ContextSummarizer,
        *,
        max_chunks: int = 8,
        job_limits: SummaryJobLimits | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ):
        if summarizer.context_budget is None:
            raise ValueError("分块摘要需要配置摘要预算")

        if type(max_chunks) is not int or max_chunks < 1:
            raise ValueError("max_chunks 必须是正整数")

        self.summarizer = summarizer
        self.max_chunks = max_chunks
        self.job_limits = (
            job_limits
            if job_limits is not None
            else SummaryJobLimits(
                max_requests=max_chunks,
                max_reserved_tokens=(
                    max_chunks
                    * (
                        summarizer.context_budget.input_budget
                        + summarizer.context_budget.max_output_tokens
                    )
                ),
            )
        )
        self.is_cancelled = is_cancelled

        self.last_job_budget: SummaryJobBudget | None = None

    def summarize_with_sources(
            self,
            plan: CompactionPlan,
            *,
            previous_sources: list[SummaryArtifactSource] | None = None,
            job_budget: SummaryJobBudget | None = None,
    ) -> SummarizationResult:
        if not plan.to_summarize:
            raise ValueError("没有需要总结的新消息")

        if job_budget is None:
            job_budget = SummaryJobBudget(
                self.job_limits,
                is_cancelled=self.is_cancelled,
            )

        self.last_job_budget = job_budget

        remaining = list(plan.to_summarize)
        summary = (
            plan.previous_summary.model_copy(deep = True)
            if plan.previous_summary is not None
            else None
        )
        artifact_sources = merge_summary_artifacts(
            (
                previous_sources
                if previous_sources is not None
                else plan.previous_artifact_sources
            ),
            [],
        )

        completed_chunks = 0

        while remaining:
            check_summary_cancelled(self.is_cancelled)
            job_budget.check_active()
            if completed_chunks >= self.max_chunks:
                raise ModelError(
                    "summary_chunk_limit",
                    f"本次摘要作业达到 {self.max_chunks} 块上限。",
                )

            working_plan = replace(
                plan,
                to_summarize=remaining,
                previous_summary=summary,
            )

            chunk = select_summary_chunk(
                working_plan,
                self.summarizer
            )

            chunk_plan = replace(
                working_plan,
                to_summarize=chunk,
            )

            result = self.summarizer.summarize_with_sources(
                chunk_plan,
                previous_sources=artifact_sources,
                job_budget=job_budget,
            )

            summary = result.summary
            artifact_sources = result.artifact_sources

            # 只有本块成功，才推进到后面的历史。
            remaining = remaining[len(chunk):]
            completed_chunks += 1

        assert summary is not None
        check_summary_cancelled(self.is_cancelled)
        job_budget.check_active()

        return SummarizationResult(
            summary=summary,
            artifact_sources=artifact_sources,
        )

    def summarize(self, plan: CompactionPlan) -> ContextSummary:
        return self.summarize_with_sources(plan).summary