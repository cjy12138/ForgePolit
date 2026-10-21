from forgepilot.exceptions import ModelError
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.sessions.budget.token_estimator import estimate_request_tokens

from tests.models.check_model_usage import UsageClient
from tests.context.check_context_overflow_error import ErrorClient


def main():
    client = UsageClient()
    model = DeepSeekModel(client)

    messages = [{"role": "user", "content": "测试"}]
    tools = []

    raw_estimate = estimate_request_tokens(
        model.prepare_request(messages, tools)
    )

    assert model.estimate_input_tokens(messages, tools) == raw_estimate
    assert model.token_calibrator.samples == 0
    print("首次请求前使用原始估算：通过")

    # 模拟实际输入是原始估算的两倍。
    client.usage = {
        "prompt_tokens": raw_estimate * 2,
        "completion_tokens": 30,
        "total_tokens": raw_estimate * 2 + 30,
    }

    model.generate(messages, tools)

    measurement = model.last_measurement
    assert measurement is not None
    assert measurement.estimated_input_tokens == raw_estimate

    calibrated = model.estimate_input_tokens(messages, tools)

    assert calibrated == model.token_calibrator.apply(raw_estimate)
    assert calibrated > raw_estimate * 2
    assert model.token_calibrator.samples == 1
    print("实际用量更新后，预算使用校准估算：通过")

    # 相同请求再次返回相同用量，不应把校准值作为原始估算。
    model.generate(messages, tools)

    assert model.last_measurement.estimated_input_tokens == raw_estimate
    assert model.estimate_input_tokens(messages, tools) == calibrated
    assert model.token_calibrator.samples == 2
    print("重复请求不会重复放大校准系数：通过")

    client.usage = None
    model.generate(messages, tools)

    assert model.last_usage is None
    assert model.token_calibrator.samples == 2
    assert model.estimate_input_tokens(messages, tools) == calibrated
    print("缺少 usage 时保留已有系数，不新增样本：通过")

    model.client = ErrorClient(
        400,
        {"error": {"code": "context_length_exceeded"}},
    )

    try:
        model.generate(messages, tools)
    except ModelError as exc:
        assert exc.code == "context_overflow"
    else:
        raise AssertionError("应报告上下文超限")

    assert model.last_measurement.usage is None
    assert model.token_calibrator.samples == 2
    assert model.estimate_input_tokens(messages, tools) == calibrated
    print("失败请求不更新校准系数：通过")

    # 即使模型名称相同，另一个适配器实例也有独立状态。
    summary_model = DeepSeekModel(UsageClient())

    assert summary_model.token_calibrator.samples == 0
    assert summary_model.estimate_input_tokens(messages, tools) == raw_estimate
    print("业务与摘要模型实例的校准状态独立：通过")


if __name__ == "__main__":
    main()