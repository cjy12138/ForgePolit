from forgepilot.sessions.budget.context_budget import ContextBudget


def main():
    budget = ContextBudget(
        context_window=32000,
        max_output_tokens=4000,
        safety_margin=2000,
        compact_ratio=0.9,
    )

    assert budget.input_budget == 26000
    assert budget.compact_at == 23400

    below = budget.assess(23399)
    assert not below.should_compact
    assert not below.exceeds_budget

    at_trigger = budget.assess(23400)
    assert at_trigger.should_compact
    assert not at_trigger.exceeds_budget
    print("达到触发点才建议压缩：通过")

    at_limit = budget.assess(26000)
    assert at_limit.should_compact
    assert not at_limit.exceeds_budget
    assert at_limit.remaining_tokens == 0

    over_limit = budget.assess(26001)
    assert over_limit.should_compact
    assert over_limit.exceeds_budget
    assert over_limit.remaining_tokens == 0
    print("区分压缩触发点与输入预算上限：通过")

    # 输出预留增加时，允许发送的输入必须减少。
    larger_output = ContextBudget(
        context_window=32000,
        max_output_tokens=8000,
        safety_margin=2000,
    )

    assert larger_output.input_budget == 22000
    assert larger_output.assess(23000).exceeds_budget
    assert not budget.assess(23000).exceeds_budget
    print("输出预留参与输入预算计算：通过")

    for invalid in (-1, True, 1.5):
        try:
            budget.assess(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("应拒绝非法 Token 数量")

    try:
        ContextBudget(
            context_window=6000,
            max_output_tokens=4000,
            safety_margin=2000,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("应拒绝没有可用输入预算的配置")

    print("拒绝非法计数及不可用配置：通过")


if __name__ == "__main__":
    main()