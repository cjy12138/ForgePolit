from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.models import CompactionEntry
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from collections.abc import Callable

from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.models import SessionNode
from forgepilot.sessions.compaction.planning import validate_compaction_entry
from forgepilot.sessions.cache.candidates import SummaryCandidateCache
from forgepilot.sessions.control.stats import ContextStats
from forgepilot.exceptions import ModelError
from forgepilot.sessions.summarization.artifacts import (
    validate_summary_result_sources,
)
from forgepilot.sessions.budget.summary_job import check_summary_cancelled
from forgepilot.sessions.compaction.collapse_boundary import (
    align_compaction_with_collapse,
)
class SessionCompactor:
    def __init__(
            self,
            store: SessionStore,
            summarizer: ContextSummarizer | ChunkedSummarizer,
            *,
            plan_builder: (
                    Callable[[list[SessionNode]], CompactionPlan | None]
                    | None
            ) = None,
            publication_check: (
                    Callable[[list[SessionNode], CompactionEntry], None]
                    | None
            ) = None,
            candidate_cache: SummaryCandidateCache | None = None,
            stats: ContextStats | None = None,
            is_cancelled: Callable[[], bool] | None = None,
    ):
        self.store = store
        self.summarizer = summarizer
        self.plan_builder = plan_builder
        self.publication_check = publication_check
        self.candidate_cache = candidate_cache
        self.stats = stats if stats is not None else ContextStats()
        self.is_cancelled = is_cancelled

    def compact(
            self,
            *,
            keep_recent_groups: int,
    ) -> CompactionEntry|None:
        check_summary_cancelled(self.is_cancelled)
        branch = self.store.get_current_branch()

        if self.plan_builder is not None:
            plan = self.plan_builder(branch)
        else:
            plan = plan_compaction(
                branch,
                keep_recent_groups=keep_recent_groups,
            )

        if plan is None:
            return None

        plan = align_compaction_with_collapse(
            plan,
            branch,
            self.store.get_current_collapse_records(),
        )

        if plan is None:
            return None

        result = (
            self.candidate_cache.get_result(plan)
            if self.candidate_cache is not None
            else None
        )
        check_summary_cancelled(self.is_cancelled)

        cache_hit = result is not None

        if cache_hit:
            self.stats.summary_cache_hits += 1

        generation_job = None

        if result is None:
            result = self.summarizer.summarize_with_sources(plan)

            if isinstance(self.summarizer, ChunkedSummarizer):
                generation_job = self.summarizer.last_job_budget

        try:
            validate_summary_result_sources(plan, result)
        except ValueError as exc:
            raise ModelError(
                "invalid_summary_sources",
                f"摘要产物来源不完整或不一致：{exc}",
            ) from exc

        entry = CompactionEntry(
            session_id=branch[-1].session_id,
            parent_id=plan.source_leaf_id,
            source_leaf_id=plan.source_leaf_id,
            first_kept_entry_id=plan.first_kept_entry_id,
            summary=result.summary,
            artifact_sources=result.artifact_sources,
            artifact_sources_version=2,
        )

        validate_compaction_entry(entry, branch)

        if self.candidate_cache is not None and not cache_hit:
            self.candidate_cache.put_result(plan, result)

        if generation_job is not None:
            generation_job.check_active()

        if self.publication_check is not None:
            self.publication_check(branch, entry)

        if generation_job is not None:
            generation_job.check_active()

        check_summary_cancelled(self.is_cancelled)

        self.store.append(entry)
        return entry

