from dataclasses import dataclass

from forgepilot.exceptions import ModelError
from collections.abc import Callable
from math import isfinite
from time import monotonic

@dataclass(frozen=True)
class SummaryJobLimits:
    max_requests: int
    max_reserved_tokens: int
    max_elapsed_seconds: float = 300.0

    def __post_init__(self):
        for name, value in (
            ("max_requests", self.max_requests),
            ("max_reserved_tokens", self.max_reserved_tokens),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} 必须是正整数")

        seconds = self.max_elapsed_seconds

        if (
            type(seconds) not in (int, float)
            or not isfinite(seconds)
            or seconds <= 0
        ):
            raise ValueError("max_elapsed_seconds 必须是有限正数")

def check_summary_cancelled(
    is_cancelled: Callable[[], bool] | None,
) -> None:
    if is_cancelled is not None and is_cancelled():
        raise ModelError(
            "summary_job_cancelled",
            "摘要作业已取消，停止继续处理和发布。",
        )

class SummaryJobBudget:
    def __init__(
            self,
            limits: SummaryJobLimits,
            *,
            clock: Callable[[], float] | None = None,
            is_cancelled: Callable[[], bool] | None = None,
    ):
        self.limits = limits
        self.requests = 0
        self.reserved_tokens = 0

        self._clock = clock if clock is not None else monotonic
        self._started_at = self._clock()
        self._is_cancelled = is_cancelled

    def reserve(
        self,
        *,
        estimated_input_tokens: int,
        max_output_tokens: int,
    ) -> None:
        self.check_active()
        for name, value in (
            ("estimated_input_tokens", estimated_input_tokens),
            ("max_output_tokens", max_output_tokens),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} 必须是正整数")

        cost = estimated_input_tokens + max_output_tokens

        if self.requests >= self.limits.max_requests:
            raise ModelError(
                "summary_job_request_limit",
                "摘要作业请求次数已达到上限，请求未发送。",
            )

        if (
            self.reserved_tokens + cost
            > self.limits.max_reserved_tokens
        ):
            raise ModelError(
                "summary_job_token_limit",
                (
                    f"摘要作业已预留 {self.reserved_tokens} Token，"
                    f"本次还需预留 {cost} Token，"
                    f"超过总上限 {self.limits.max_reserved_tokens}。"
                    "请求未发送。"
                ),
            )

        self.requests += 1
        self.reserved_tokens += cost

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    def check_active(self) -> None:
        check_summary_cancelled(self._is_cancelled)
        if self.elapsed_seconds >= self.limits.max_elapsed_seconds:
            raise ModelError(
                "summary_job_timeout",
                "摘要作业超过总时间预算，停止继续处理和发布。",
            )