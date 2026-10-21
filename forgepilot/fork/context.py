from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.budget.summary_job import SummaryJobLimits
from forgepilot.sessions.cache.candidates import SummaryCandidateCache
from forgepilot.sessions.cache.profile import build_summary_cache_profile
from forgepilot.sessions.cache.snapshot import SummarySnapshotStore
from forgepilot.sessions.collapse.collapser import SessionCollapser
from forgepilot.sessions.collapse.selector import CollapseSelector
from forgepilot.sessions.collapse.summarizer import CollapseSummarizer
from forgepilot.sessions.compaction.benefit import CompactionBenefitCheck
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.compaction.token_planning import plan_compaction_by_tokens
from forgepilot.sessions.control.circuit_state import CircuitStateStore
from forgepilot.sessions.control.controller import ContextController
from forgepilot.sessions.control.stats import ContextStats
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.summarization.summarizer import ContextSummarizer


SUBAGENT_SYSTEM_PROMPT = (
    "你是已经由主 Agent 派发启动的子 Agent。父历史只提供背景；"
    "其中要求主 Agent 调用 fork_tasks 的操作已经完成，不是你的待办。"
    "你只负责当前子任务指令，使用提供的工具执行并返回结论和未验证事项。"
    "不要再次派发，不要因为没有 fork_tasks 工具就宣称派发失败或当前任务无法执行。"
)


def build_fork_context(*, client, model, tools, session, artifacts, budget,
                       skill_messages, enable_snip=True):
    stats = ContextStats()
    # 使用项目预算，并非模型官方上下文规格。
    summary_budget = ContextBudget(context_window=32000, max_output_tokens=4096, safety_margin=2000)
    selection_budget = ContextBudget(context_window=32000, max_output_tokens=512, safety_margin=2000)
    limits = SummaryJobLimits(max_requests=8, max_reserved_tokens=96000, max_elapsed_seconds=180.0)

    def worker():
        return ChunkedSummarizer(ContextSummarizer(
            DeepSeekModel(client, model_name=model.model_name, max_output_tokens=4096),
            context_budget=summary_budget, artifact_store=artifacts, stats=stats,
        ), max_chunks=8, job_limits=limits)

    prefix_worker = worker()
    cache = SummaryCandidateCache(
        profile=build_summary_cache_profile(prefix_worker, provider_id="https://api.deepseek.com"),
        max_entries=16,
        snapshot_store=SummarySnapshotStore(session.path.parent / "summary-candidate.json"),
    )
    compactor = SessionCompactor(
        session, prefix_worker,
        plan_builder=lambda branch: plan_compaction_by_tokens(
            branch, model=model, tools=tools,
            target_input_tokens=int(budget.compact_at * 0.8),
            summary_reserve_tokens=4096,
            collapse_records=session.get_current_collapse_records(),
        ),
        publication_check=CompactionBenefitCheck(
            model=model, tools=tools, max_input_tokens=budget.input_budget,
            minimum_savings_tokens=256, artifacts=artifacts,
        ),
        candidate_cache=cache, stats=stats,
    )
    collapser = SessionCollapser(
        store=session,
        selector=CollapseSelector(DeepSeekModel(
            client, model_name=model.model_name, max_output_tokens=512,
        ), context_budget=selection_budget),
        summarizer=CollapseSummarizer(worker()), model=model, tools=tools,
        artifacts=artifacts, job_limits=limits,
    )
    header, _ = session.read()
    return ContextController(
        model=model, tools=tools, store=session, budget=budget, artifacts=artifacts,
        compactor=compactor, collapser=collapser, stats=stats, enable_snip=enable_snip,
        circuit_store=CircuitStateStore(session.path.parent / "context-state.json", header.session_id),
        request_prefix_provider=lambda: [
            *[dict(item) for item in skill_messages],
            {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
        ],
    )
