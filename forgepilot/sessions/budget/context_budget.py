from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetDecision:
    input_tokens: int
    input_budget: int
    compact_at: int

    @property
    def should_compact(self) -> bool:
        return self.input_tokens >= self.compact_at

    @property
    def exceeds_budget(self) -> bool:
        return self.input_tokens > self.input_budget

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.input_budget - self.input_tokens)


@dataclass(frozen=True)
class ContextBudget:
    context_window: int
    max_output_tokens: int
    safety_margin: int
    compact_ratio: float = 0.9

    def __post_init__(self):
        for name in (
            "context_window",
            "max_output_tokens",
            "safety_margin",
        ):
            value = getattr(self, name)

            if type(value) is not int:
                raise ValueError(f"{name} 必须是整数")

        if self.context_window <= 0:
            raise ValueError("context_window 必须大于 0")

        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens 必须大于 0")

        if self.safety_margin < 0:
            raise ValueError("safety_margin 不能小于 0")

        if (
            type(self.compact_ratio) not in (int, float)
            or not 0 < self.compact_ratio < 1
        ):
            raise ValueError("compact_ratio 必须在 0 和 1 之间")

        if self.input_budget <= 0:
            raise ValueError("输出预留和安全余量耗尽了上下文窗口")

    @property
    def input_budget(self) -> int:
        return (
            self.context_window
            - self.max_output_tokens
            - self.safety_margin
        )

    @property
    def compact_at(self) -> int:
        return max(
            1,
            int(self.input_budget * self.compact_ratio),
        )

    def assess(self, input_tokens: int) -> BudgetDecision:
        if type(input_tokens) is not int or input_tokens < 0:
            raise ValueError("input_tokens 必须是非负整数")

        return BudgetDecision(
            input_tokens=input_tokens,
            input_budget=self.input_budget,
            compact_at=self.compact_at,
        )