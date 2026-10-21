from collections.abc import Callable

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.summary_job import (
    SummaryJobBudget,
    SummaryJobLimits,
)
from forgepilot.sessions.collapse.records import CollapseRecord
from forgepilot.sessions.collapse.request_view import (
    build_layered_request,
)
from forgepilot.sessions.collapse.selector import CollapseSelector
from forgepilot.sessions.collapse.summarizer import (
    CollapseSummarizer,
    build_collapse_record,
)
from forgepilot.sessions.store import SessionStore
from dataclasses import dataclass

from forgepilot.sessions.models import SessionNode
from forgepilot.sessions.snip.records import SnipRecord
from forgepilot.sessions.collapse.planning import plan_collapse
from forgepilot.sessions.collapse.validation import (
    validate_collapse_record,
)


@dataclass(frozen=True)
class PreparedCollapse:
    record: CollapseRecord
    job_budget: SummaryJobBudget

class SessionCollapser:
    def __init__(
        self,
        *,
        store: SessionStore,
        selector: CollapseSelector,
        summarizer: CollapseSummarizer,
        model,
        tools,
        artifacts: ArtifactStore,
        job_limits: SummaryJobLimits,
        minimum_savings_tokens: int = 256,
        is_cancelled: Callable[[], bool] | None = None,
    ):
        if (
            type(minimum_savings_tokens) is not int
            or minimum_savings_tokens < 1
        ):
            raise ValueError("最小节省量必须是正整数")

        self.store = store
        self.selector = selector
        self.summarizer = summarizer
        self.model = model
        self.tools = tools
        self.artifacts = artifacts
        self.job_limits = job_limits
        self.minimum_savings_tokens = minimum_savings_tokens
        self.is_cancelled = is_cancelled

        self.last_job_budget: SummaryJobBudget | None = None
        self.last_savings_tokens: int | None = None

    def prepare_candidate(
            self,
            *,
            branch: list[SessionNode],
            records: list[CollapseRecord],
            snip_records: list[SnipRecord],
            enable_snip: bool = True,
    ) -> PreparedCollapse | None:
        self.last_savings_tokens = None

        job = SummaryJobBudget(
            self.job_limits,
            is_cancelled=self.is_cancelled,
        )
        self.last_job_budget = job
        job.check_active()

        branch = [
            node.model_copy(deep=True)
            for node in branch
        ]
        records = [
            record.model_copy(deep=True)
            for record in records
        ]
        snip_records = [
            record.model_copy(deep=True)
            for record in snip_records
        ]

        before_messages = build_layered_request(
            branch,
            self.artifacts,
            snip_records,
            records,
            enable_snip=enable_snip,
        )

        plan = self.selector.select(
            branch,
            records,
            context_messages=before_messages,
            job_budget=job,
        )

        job.check_active()

        if plan is None:
            return None

        candidate = self.summarizer.summarize(
            plan,
            job_budget=job,
        )

        record = build_collapse_record(
            candidate,
            session_id=branch[-1].session_id,
        )

        job.check_active()

        return PreparedCollapse(
            record=record,
            job_budget=job,
        )

    def publish_candidate(
        self,
        prepared: PreparedCollapse,
        *,
        enable_snip: bool = True,
    ) -> CollapseRecord | None:
        self.last_savings_tokens = None

        record = prepared.record
        job = prepared.job_budget
        job.check_active()

        current_branch = self.store.get_current_branch()

        positions = {
            node.id: index
            for index, node in enumerate(current_branch)
        }

        if record.source_leaf_id not in positions:
            # 当前分支不再包含准备时的来源终点。
            return None

        # 检查候选本身及它准备时的来源。
        validate_collapse_record(record, current_branch)

        try:
            current_plan = plan_collapse(
                current_branch,
                source_start_id=record.source_start_id,
                source_end_id=record.source_end_id,
            )
        except ValueError:
            # 例如范围已经被前缀摘要覆盖，
            # 或当前尚未到完整工具交互边界。
            return None

        if current_plan.source_sha256 != record.source_sha256:
            return None

        prepared_leaf_id = (
            record.prepared_leaf_id
            if record.prepared_leaf_id is not None
            else record.source_leaf_id
        )

        # 保留准备来源，发布锚点改为当前终点。
        record = record.model_copy(
            deep=True,
            update={
                "prepared_leaf_id": prepared_leaf_id,
                "source_leaf_id": current_branch[-1].id,
            },
        )

        current_records = self.store.get_current_collapse_records()
        candidate_ids = {
            node.id
            for node in current_plan.to_summarize
        }

        for existing in current_records:
            start = positions[existing.source_start_id]
            end = positions[existing.source_end_id]

            existing_ids = {
                node.id
                for node in current_branch[start:end + 1]
            }

            if candidate_ids & existing_ids:
                return None
        current_snips = (
            self.store.get_current_snip_records()
            if enable_snip
            else []
        )

        before_messages = build_layered_request(
            current_branch,
            self.artifacts,
            current_snips,
            current_records,
            enable_snip=enable_snip,
        )

        after_messages = build_layered_request(
            current_branch,
            self.artifacts,
            current_snips,
            [*current_records, record],
            enable_snip=enable_snip,
        )

        before_tokens = self.model.estimate_input_tokens(
            messages=before_messages,
            tools=self.tools,
        )
        after_tokens = self.model.estimate_input_tokens(
            messages=after_messages,
            tools=self.tools,
        )

        savings = before_tokens - after_tokens
        self.last_savings_tokens = savings

        if savings < self.minimum_savings_tokens:
            raise ModelError(
                "collapse_insufficient_savings",
                (
                    f"候选折叠预计节省 {savings} Token，"
                    f"低于最小收益 {self.minimum_savings_tokens} Token。"
                    "记录未发布。"
                ),
            )

        job.check_active()

        if not self.store.append_collapse(record):
            return None

        return record

    def collapse(
        self,
        *,
        enable_snip: bool = True,
    ) -> CollapseRecord | None:
        prepared = self.prepare_candidate(
            branch=self.store.get_current_branch(),
            records=self.store.get_current_collapse_records(),
            snip_records=(
                self.store.get_current_snip_records()
                if enable_snip
                else []
            ),
            enable_snip=enable_snip,
        )

        if prepared is None:
            return None

        return self.publish_candidate(
            prepared,
            enable_snip=enable_snip,
        )