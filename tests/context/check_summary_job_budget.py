from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.summary_job import (
    SummaryJobBudget,
    SummaryJobLimits,
)


def expect_error(budget, expected_code):
    before = (budget.requests, budget.reserved_tokens)

    try:
        budget.reserve(
            estimated_input_tokens=200,
            max_output_tokens=100,
        )
    except ModelError as exc:
        assert exc.code == expected_code
    else:
        raise AssertionError("应拒绝新的摘要请求")

    assert (budget.requests, budget.reserved_tokens) == before


def main():
    budget = SummaryJobBudget(SummaryJobLimits(
        max_requests=3,
        max_reserved_tokens=600,
    ))

    budget.reserve(
        estimated_input_tokens=200,
        max_output_tokens=100,
    )
    assert budget.requests == 1
    assert budget.reserved_tokens == 300

    budget.reserve(
        estimated_input_tokens=200,
        max_output_tokens=100,
    )
    assert budget.requests == 2
    assert budget.reserved_tokens == 600
    print("输入和最大输出共同计入累计预留，允许恰好达到上限：通过")

    expect_error(budget, "summary_job_token_limit")
    print("累计 Token 超限时拒绝，计数不变：通过")

    request_limited = SummaryJobBudget(SummaryJobLimits(
        max_requests=1,
        max_reserved_tokens=10000,
    ))
    request_limited.reserve(
        estimated_input_tokens=200,
        max_output_tokens=100,
    )

    expect_error(request_limited, "summary_job_request_limit")
    print("剩余 Token 足够时仍遵守请求次数上限：通过")

    fresh = SummaryJobBudget(budget.limits)
    assert fresh.requests == 0
    assert fresh.reserved_tokens == 0
    assert budget.reserved_tokens == 600
    print("不同摘要作业的计数独立：通过")

    for invalid in (0, -1, True, 1.5):
        try:
            SummaryJobLimits(
                max_requests=invalid,
                max_reserved_tokens=1000,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("非法上限被接受")

    print("拒绝非法预算配置：通过")


if __name__ == "__main__":
    main()