from dataclasses import dataclass
from typing import Any

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import BudgetDecision, ContextBudget
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.context import build_request_messages
from forgepilot.sessions.snip.request_view import build_snipped_request
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.control.stats import ContextStats
from forgepilot.sessions.control.circuit_state import CircuitStateStore
from collections.abc import Callable
from forgepilot.sessions.files.budget import FileRestoration
from forgepilot.sessions.models import CompactionEntry, SessionNode
from forgepilot.sessions.snip.request_view import build_recorded_snip_request
from forgepilot.sessions.snip.planner import plan_snip_records
from forgepilot.sessions.control.outcomes import is_compaction_stop
from forgepilot.sessions.collapse.request_view import (
    build_layered_request,
)
from forgepilot.sessions.collapse.collapser import SessionCollapser
from forgepilot.sessions.collapse.background import BackgroundCollapse
COLLAPSE_SKIP_CODES = frozenset({
    "collapse_insufficient_savings",
    "collapse_selection_budget_exceeded",
    "summary_job_timeout",
    "summary_job_request_limit",
    "summary_job_token_limit",
    "summary_chunk_limit",
    "summary_budget_exceeded",
    "summary_group_too_large",
})
@dataclass
class PreparedContext:
    messages: list[dict[str, Any]]
    decision: BudgetDecision
    actions: list[str]

class ContextController:
    def __init__(
        self,
        *,
        model,
        tools,
        store: SessionStore,
        budget: ContextBudget,
        artifacts: ArtifactStore,
        compactor: SessionCompactor | None = None,
        enable_snip: bool = True,
        snip_ratio: float = 0.7,
        keep_recent_groups: int = 3,
        stats: ContextStats | None = None,
        max_compaction_failures: int = 3,
        circuit_store: CircuitStateStore | None = None,
        file_restorer: (
                Callable[[list[SessionNode], list[dict]], FileRestoration]
                | None
        ) = None,
        collapser: SessionCollapser | None = None,
        collapse_ratio: float = 0.8,
        background_collapse: BackgroundCollapse | None = None,
        request_prefix_provider: (
                Callable[[], list[dict]] | None
        ) = None,
    ):
        if (
            type(snip_ratio) not in (int, float)
            or not 0 < snip_ratio < budget.compact_ratio
        ):
            raise ValueError("Snip 比例必须小于摘要压缩比例")

        if type(enable_snip) is not bool:
            raise ValueError("enable_snip 必须是布尔值")

        if type(keep_recent_groups) is not int or keep_recent_groups < 1:
            raise ValueError("keep_recent_groups 必须是正整数")

        if not callable(getattr(model, "estimate_input_tokens", None)):
            raise ValueError("模型必须支持输入估算")

        if getattr(model, "max_output_tokens", None) != budget.max_output_tokens:
            raise ValueError("模型输出上限与预算预留不一致")

        if compactor is not None and compactor.store is not store:
            raise ValueError("控制器与压缩器必须使用同一个存储对象")

        if enable_snip and not any(
            tool["name"] == "read_artifact"
            for tool in tools
        ):
            raise ValueError("启用 Snip 必须提供 read_artifact")

        if background_collapse is not None:
            if collapser is None or background_collapse.collapser is not collapser:
                raise ValueError("后台工作器必须使用控制器注入的 Collapse 执行器")
        self.background_collapse = background_collapse
        self.model = model
        self.tools = tools
        self.store = store
        self.budget = budget
        self.artifacts = artifacts
        self.compactor = compactor
        self.enable_snip = enable_snip
        self.snip_ratio = snip_ratio
        self.keep_recent_groups = keep_recent_groups
        self.stats = stats if stats is not None else ContextStats()
        if (
            type(max_compaction_failures) is not int
            or max_compaction_failures < 1
        ):
            raise ValueError("失败上限必须是正整数")

        self.max_compaction_failures = max_compaction_failures
        self.circuit_store = circuit_store

        if circuit_store is not None:
            header, _ = store.read()

            if circuit_store.session_id != header.session_id:
                raise ValueError("控制器与熔断状态必须属于同一会话")

            self.consecutive_compaction_failures = (
                circuit_store.load().consecutive_failures
            )
        else:
            self.consecutive_compaction_failures = 0
        self.file_restorer = file_restorer
        if collapser is not None:
            if collapser.store is not store:
                raise ValueError(
                    "控制器与 Collapse 执行器必须使用同一个存储对象"
                )

            if (
                type(collapse_ratio) not in (int, float)
                or not snip_ratio < collapse_ratio < budget.compact_ratio
            ):
                raise ValueError(
                    "Collapse 比例必须位于 Snip 与前缀压缩之间"
                )
        self.collapser = collapser
        self.collapse_ratio = collapse_ratio
        if (
            request_prefix_provider is not None
            and not callable(request_prefix_provider)
        ):
            raise ValueError(
                "request_prefix_provider 必须可调用"
            )

        self.request_prefix_provider = request_prefix_provider
        self._request_prefix = []

    def set_compaction_failures(self, count: int) -> None:
        # 更新失败时，先保持内存中较保守的状态。
        previous = self.consecutive_compaction_failures

        if count > previous:
            self.consecutive_compaction_failures = count

        if self.circuit_store is not None:
            self.circuit_store.save(count)

        self.consecutive_compaction_failures = count


    def assess(self, messages):
        estimated = self.model.estimate_input_tokens(
            messages=[
                *self._request_prefix,
                *messages,
            ],
            tools=self.tools,
        )
        return self.budget.assess(estimated)

    @property
    def compaction_circuit_open(self) -> bool:
        return (
            self.consecutive_compaction_failures
            >= self.max_compaction_failures
        )

    def reset_compaction_circuit(self) -> None:
        self.set_compaction_failures(0)

    def prepare(
            self,
            *,
            force_compaction: bool = False,
            restore_files: bool = True,
    ) -> PreparedContext:
        self._request_prefix = (
            self.request_prefix_provider()
            if self.request_prefix_provider is not None
            else []
        )
        actions = []
        if self.background_collapse is not None:
            try:
                status, _ = self.background_collapse.commit_ready()
            except ModelError as exc:
                if exc.code in COLLAPSE_SKIP_CODES:
                    self.stats.collapse_skips += 1
                    actions.append(f"collapse_skipped:{exc.code}")
                else:
                    if exc.code != "summary_job_cancelled":
                        self.stats.collapse_failures += 1
                    raise
            else:
                if status == "published":
                    self.stats.collapse_successes += 1
                    actions.append("collapse")
                elif status == "skipped":
                    self.stats.collapse_skips += 1
                    actions.append("collapse_skipped")

        branch = self.store.get_current_branch()
        raw_messages = build_request_messages(branch)
        messages = self.build_context_view(branch)
        decision = self.assess(messages)

        snip_messages = (
            build_recorded_snip_request(
                branch,
                self.artifacts,
                self.store.get_current_snip_records(),
                keep_recent_tool_groups=2,
            )
            if self.enable_snip
            else raw_messages
        )

        if snip_messages != raw_messages:
            actions.append("snip_restored")

        if messages != snip_messages:
            actions.append("collapse_restored")

        snip_at = max(
            1,
            int(self.budget.input_budget * self.snip_ratio),
        )

        if self.enable_snip and (
                force_compaction or decision.input_tokens >= snip_at
        ):
            self.publish_snip_records(branch)
            shortened = self.build_context_view(branch)

            if shortened != messages:
                messages = shortened
                decision = self.assess(messages)
                actions.append("snip")
                self.stats.snip_applications += 1

        collapse_at = max(
            1,
            int(self.budget.input_budget * self.collapse_ratio),
        )

        if (
            self.background_collapse is not None
            and not force_compaction
            and not decision.should_compact
            and decision.input_tokens >= collapse_at
        ):
            if self.background_collapse.start(enable_snip=self.enable_snip):
                self.stats.collapse_attempts += 1
                actions.append("collapse_started")

        if (
            self.collapser is not None
            and self.background_collapse is None
            and (
                force_compaction
                or decision.input_tokens >= collapse_at
            )
        ):
            self.stats.collapse_attempts += 1

            try:
                record = self.collapser.collapse(
                    enable_snip=self.enable_snip,
                )

            except ModelError as exc:
                if exc.code == "summary_job_cancelled":
                    raise

                if exc.code in COLLAPSE_SKIP_CODES:
                    self.stats.collapse_skips += 1
                    actions.append(f"collapse_skipped:{exc.code}")
                else:
                    self.stats.collapse_failures += 1
                    raise

            else:
                if record is None:
                    self.stats.collapse_skips += 1
                    actions.append("collapse_skipped")
                else:
                    self.stats.collapse_successes += 1
                    actions.append("collapse")

                    branch = self.store.get_current_branch()
                    messages = self.build_context_view(branch)
                    decision = self.assess(messages)

        if (
                self.compactor is not None
                and (force_compaction or decision.should_compact)
        ):
            if self.compaction_circuit_open:
                actions.append("compact_skipped_circuit_open")
            else:
                self.stats.compaction_attempts += 1

                try:
                    entry = self.compactor.compact(
                        keep_recent_groups=self.keep_recent_groups,
                    )
                except ModelError as exc:
                    if is_compaction_stop(exc.code):
                        self.stats.compaction_stops += 1
                        raise

                    self.stats.compaction_failures += 1
                    self.set_compaction_failures(
                        self.consecutive_compaction_failures + 1
                    )
                    raise
                except Exception:
                    self.stats.compaction_failures += 1
                    raise

                if entry is not None:
                    self.set_compaction_failures(0)
                    self.stats.compaction_successes += 1
                    actions.append("compact")
                    branch = self.store.get_current_branch()

                    messages = self.build_context_view(branch)

                    decision = self.assess(messages)

        if decision.exceeds_budget:
            if self.compaction_circuit_open:
                raise ModelError(
                    "compaction_circuit_open",
                    (
                        "自动压缩已因连续失败暂停，"
                        f"当前估算输入 {decision.input_tokens} Token，"
                        f"仍超过预算 {decision.input_budget} Token。"
                        "业务请求未发送。"
                    ),
                )

            raise ModelError(
                "context_budget_exceeded",
                (
                    f"处理后估算输入为 {decision.input_tokens} Token，"
                    f"超过输入预算 {decision.input_budget} Token。"
                    "业务请求未发送。"
                ),
            )

        if (
            restore_files
            and self.file_restorer is not None
            and any(
                isinstance(node, CompactionEntry)
                for node in branch
            )
        ):
            restored = self.file_restorer(branch, messages)
            messages = restored.messages

            # 以最终加入文件数据后的请求重新判断。
            decision = self.assess(messages)

            if decision.exceeds_budget:
                raise ModelError(
                    "file_restore_over_budget",
                    "文件恢复后的请求超过输入预算，业务请求未发送。",
                )

            if restored.restored_paths:
                actions.append("file_restore")

        return PreparedContext(
            messages=[
                *self._request_prefix,
                *messages,
            ],
            decision=decision,
            actions=actions,
        )

    def build_context_view(self, branch):
        return build_layered_request(
            branch,
            self.artifacts,
            (
                self.store.get_current_snip_records()
                if self.enable_snip
                else []
            ),
            self.store.get_current_collapse_records(),
            enable_snip=self.enable_snip,
        )

    def publish_snip_records(self, branch) -> int:
        existing = self.store.get_current_snip_records()

        # 后代位置再次准备请求时，不为相同目标和策略重复写记录。
        existing_keys = {
            (
                record.target_entry_id,
                record.source_sha256,
                record.policy_version,
                record.head_chars,
                record.tail_chars,
            )
            for record in existing
        }

        published = 0

        for candidate in plan_snip_records(branch, self.artifacts):
            key = (
                candidate.target_entry_id,
                candidate.source_sha256,
                candidate.policy_version,
                candidate.head_chars,
                candidate.tail_chars,
            )

            if key in existing_keys:
                continue

            if self.store.append_snip(candidate):
                published += 1

            existing_keys.add(key)

        return published
