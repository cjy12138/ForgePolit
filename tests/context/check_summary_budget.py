from copy import deepcopy
from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_session_compactor import (
    FakeSummaryModel,
    make_store,
)


class BudgetSummaryModel(FakeSummaryModel):
    max_output_tokens = 100

    def __init__(self, estimated_tokens):
        super().__init__()
        self.estimated_tokens = estimated_tokens
        self.estimated_request = None
        self.sent_request = None

    def estimate_input_tokens(self, messages, tools):
        self.estimated_request = deepcopy({
            "messages": messages,
            "tools": tools,
        })
        return self.estimated_tokens

    def generate(self, messages, tools):
        self.sent_request = deepcopy({
            "messages": messages,
            "tools": tools,
        })
        return super().generate(messages, tools)


def main():
    budget = ContextBudget(
        context_window=1000,
        max_output_tokens=100,
        safety_margin=100,
    )
    assert budget.input_budget == 800

    with TemporaryDirectory() as directory:
        # 恰好达到输入预算，允许发送。
        store = make_store(directory, "within.jsonl")
        model = BudgetSummaryModel(estimated_tokens=800)

        compactor = SessionCompactor(
            store,
            ContextSummarizer(
                model,
                context_budget=budget,
            ),
        )

        entry = compactor.compact(keep_recent_groups=1)

        assert entry is not None
        assert model.calls == 1
        assert model.estimated_request == model.sent_request

        request = model.estimated_request
        assert request["messages"][0]["role"] == "system"
        assert request["tools"][0]["name"] == (
            "submit_context_summary"
        )
        print("摘要规则、历史及提交 Schema 一起估算：通过")

        # 超过预算，生成与发布都不能发生。
        store = make_store(directory, "over.jsonl")
        before = store.path.read_bytes()

        model = BudgetSummaryModel(estimated_tokens=801)
        compactor = SessionCompactor(
            store,
            ContextSummarizer(
                model,
                context_budget=budget,
            ),
        )

        try:
            compactor.compact(keep_recent_groups=1)
        except ModelError as exc:
            assert exc.code == "summary_budget_exceeded"
        else:
            raise AssertionError("应拒绝超预算摘要请求")

        assert model.calls == 0
        assert model.sent_request is None
        assert store.path.read_bytes() == before
        print("摘要超预算时不请求模型、不写入节点：通过")

        model.max_output_tokens = 200

        try:
            ContextSummarizer(
                model,
                context_budget=budget,
            )
        except ValueError as exc:
            assert "输出上限" in str(exc)
        else:
            raise AssertionError("应拒绝不一致的摘要输出配置")

        print("摘要输出上限与预算预留必须一致：通过")


if __name__ == "__main__":
    main()