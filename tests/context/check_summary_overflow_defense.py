"""摘要超限兜底的离线验收，不调用真实 API。"""

import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.budget.summary_job import SummaryJobBudget, SummaryJobLimits
from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.summarization.artifacts import (
    collect_summary_artifacts,
    validate_summary_result_sources,
)
from forgepilot.sessions.summarization.schema import ContextSummary, SummaryItem
from forgepilot.sessions.summarization.summarizer import ContextSummarizer, OMISSION_NOTICE


class ScriptedModel:
    max_output_tokens = 100

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.inputs = []

    def estimate_input_tokens(self, messages, tools):
        return 200

    def generate(self, messages, tools):
        data = json.loads(messages[1]["content"])
        self.inputs.append(data)
        outcome = next(self.outcomes)
        if outcome != "ok":
            raise ModelError(outcome, "模拟 API 错误")
        summary = deepcopy(data["previous_summary"])
        if summary is None:
            summary = {name: [] for name in ContextSummary.model_fields}
        # 模拟模型遗漏程序说明，检查摘要器是否会补回。
        summary["critical_context"] = [
            item for item in summary["critical_context"]
            if not item["text"].startswith(OMISSION_NOTICE)
        ]
        return {
            "type": "tool_call",
            "id": "summary_call",
            "name": "submit_context_summary",
            "arguments": summary,
        }


def make_plan():
    previous = ContextSummary(**{
        name: [] for name in ContextSummary.model_fields
    })
    for name in ContextSummary.model_fields:
        if name not in {"completed", "verification"}:
            getattr(previous, name).append(SummaryItem(
                text=f"保留 {name}", source_entry_ids=["old_0"],
            ))
    for name in ("completed", "verification"):
        for i in range(8):
            getattr(previous, name).append(SummaryItem(
                text=f"{name} 历史细节 {i} " + "内容" * (i + 1),
                source_entry_ids=[f"old_{i}"],
            ))
    message = MessageEntry(
        session_id="demo", payload={"role": "user", "content": "继续任务"},
    )
    return CompactionPlan(
        source_leaf_id=message.id,
        to_summarize=[message],
        to_keep=[message],
        previous_summary=previous,
    )


def run_case(outcomes, *, plan=None, request_limit=10, expected_error=None):
    plan = plan if plan is not None else make_plan()
    before = deepcopy(plan)
    model = ScriptedModel(outcomes)
    summarizer = ContextSummarizer(model, context_budget=ContextBudget(
        context_window=1000, max_output_tokens=100, safety_margin=100,
    ))
    job = SummaryJobBudget(SummaryJobLimits(
        max_requests=request_limit, max_reserved_tokens=10000,
    ))
    result = None
    try:
        result = summarizer.summarize_with_sources(plan, job_budget=job)
    except ModelError as exc:
        assert exc.code == expected_error, (exc.code, expected_error)
    else:
        assert expected_error is None
        validate_summary_result_sources(plan, result)
    assert plan == before, "不能修改原计划或旧摘要"
    assert job.requests == len(model.inputs)
    assert job.reserved_tokens == len(model.inputs) * 300
    assert summarizer.stats.summary_requests == len(model.inputs)
    return result, model.inputs


def main():
    result, inputs = run_case(["ok"])
    assert len(inputs) == 1
    assert not any(i.text.startswith(OMISSION_NOTICE)
                   for i in result.summary.critical_context)
    print("正常摘要只调用一次：通过")

    result, inputs = run_case(["context_overflow", "ok"])
    assert len(inputs) == 2
    assert inputs[0]["messages"] == inputs[1]["messages"]
    for name in ContextSummary.model_fields:
        if name not in {"completed", "verification"}:
            assert inputs[0]["previous_summary"][name] == inputs[1]["previous_summary"][name]
    assert len(inputs[1]["previous_summary"]["completed"]) == 4
    assert len(inputs[1]["previous_summary"]["verification"]) == 4
    assert any(i.text.startswith(OMISSION_NOTICE)
               for i in result.summary.critical_context)
    print("超限后立即削减细节，保护关键事项和新增历史：通过")

    next_plan = make_plan()
    next_plan.previous_summary = result.summary
    next_result, _ = run_case(["ok"], plan=next_plan)
    assert any(i.text.startswith(OMISSION_NOTICE)
               for i in next_result.summary.critical_context)
    print("下一次摘要仍保留省略说明：通过")

    _, inputs = run_case(["context_overflow"] * 4,
                         expected_error="summary_overflow_retry_limit")
    assert len(inputs) == 4
    assert [len(i["previous_summary"]["completed"]) for i in inputs] == [8, 4, 2, 1]
    print("纯超限最多首次加三次重试：通过")

    _, inputs = run_case(
        ["timeout", "context_overflow", "context_overflow", "context_overflow", "ok"]
    )
    assert len(inputs) == 5
    _, inputs = run_case(["timeout", "context_overflow", "timeout"], expected_error="timeout")
    assert len(inputs) == 3
    print("常规重试只共享一次额度，不产生嵌套倍增：通过")

    _, inputs = run_case(["context_overflow"] * 2, request_limit=2,
                         expected_error="summary_job_request_limit")
    assert len(inputs) == 2
    plan = make_plan()
    plan.previous_summary = None
    _, inputs = run_case(["context_overflow"], plan=plan, expected_error="context_overflow")
    assert len(inputs) == 1
    plan = make_plan()
    plan.previous_summary.completed = []
    plan.previous_summary.verification = []
    _, inputs = run_case(["context_overflow"], plan=plan,
                         expected_error="summary_overflow_cannot_trim")
    assert len(inputs) == 1
    print("共享预算、无旧摘要或无可削减细节时停止：通过")

    with TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory))
        ref = artifacts.save_text("旧测试输出")
        old = MessageEntry(session_id="demo", payload={
            "role": "tool", "content": "旧测试输出", "artifact": ref.model_dump(),
        })
        plan = make_plan()
        plan.previous_artifact_sources = collect_summary_artifacts(
            [old], [{"id": old.id, "message": old.payload}],
        )
        result, _ = run_case(["context_overflow", "ok"], plan=plan)
        assert result.artifact_sources == plan.previous_artifact_sources
    print("削减文字不丢失旧产物索引：通过")


if __name__ == "__main__":
    main()
