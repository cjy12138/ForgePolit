from types import SimpleNamespace

from forgepilot.exceptions import ModelError
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.models.usage import ModelUsage, normalize_usage

from tests.context.check_context_overflow_error import ErrorClient
from forgepilot.sessions.budget.token_estimator import estimate_request_tokens

class UsageClient:
    def __init__(self):
        self.usage = {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
        }

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    def create(self, **kwargs):
        return SimpleNamespace(
            usage=self.usage,
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        tool_calls=None,
                        content="完成",
                    ),
                )
            ],
        )


def main():
    expected = ModelUsage(
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
    )

    assert normalize_usage({
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
    }) == expected

    assert normalize_usage(SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
    )) == expected

    assert normalize_usage(None) is None
    assert normalize_usage({
        "prompt_tokens": True,
        "completion_tokens": 30,
    }) is None
    assert normalize_usage({
        "prompt_tokens": -1,
        "completion_tokens": 30,
    }) is None
    print("标准化用量，拒绝非法计数：通过")

    client = UsageClient()
    model = DeepSeekModel(client)

    response = model.generate(
        messages=[{"role": "user", "content": "测试"}],
        tools=[],
    )

    assert response == {"type": "final", "content": "完成"}
    assert model.last_usage == expected
    expected_estimate = estimate_request_tokens(
        model.prepare_request(
            [{"role": "user", "content": "测试"}],
            [],
        )
    )

    measurement = model.last_measurement

    assert measurement is not None
    assert measurement.model_name == model.model_name
    assert measurement.estimated_input_tokens == expected_estimate
    assert measurement.usage == expected
    assert measurement.input_ratio == 120 / expected_estimate

    print("估算与实际用量对应同一次请求：通过")

    long_messages = [
        {"role": "user", "content": "更长的上下文" * 1000}
    ]

    model.generate(messages=long_messages, tools=[])

    long_measurement = model.last_measurement

    assert long_measurement is not None
    assert long_measurement.estimated_input_tokens == (
        estimate_request_tokens(
            model.prepare_request(long_messages, [])
        )
    )
    assert (
        long_measurement.estimated_input_tokens
        > measurement.estimated_input_tokens
    )

    # 旧记录是不可变对象，不随下一次请求更新。
    assert measurement.estimated_input_tokens == expected_estimate
    print("下一次请求生成新记录，不混用旧估算：通过")
    print("保存实际用量，不改变动作响应格式：通过")

    client.usage = None
    model.generate(
        messages=[{"role": "user", "content": "继续"}],
        tools=[],
    )

    assert model.last_usage is None
    print("缺少 usage 时不沿用上次用量：通过")
    assert model.last_measurement is not None
    assert model.last_measurement.usage is None
    assert model.last_measurement.input_ratio is None
    # 先恢复一次成功用量，再模拟下一次 API 失败。
    client.usage = {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
    }
    model.generate(
        messages=[{"role": "user", "content": "测试"}],
        tools=[],
    )
    assert model.last_usage == expected

    model.client = ErrorClient(
        400,
        {"error": {"code": "context_length_exceeded"}},
    )

    try:
        model.generate(
            messages=[{"role": "user", "content": "测试"}],
            tools=[],
        )
    except ModelError as exc:
        assert exc.code == "context_overflow"
    else:
        raise AssertionError("应报告上下文超限")

    assert model.last_usage is None
    print("请求失败后不会残留旧用量：通过")


if __name__ == "__main__":
    main()