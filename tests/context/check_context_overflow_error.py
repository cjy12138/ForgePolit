from types import SimpleNamespace

import httpx
from openai import APIStatusError

from forgepilot.exceptions import ModelError
from forgepilot.models.deepseek import DeepSeekModel


class ErrorClient:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        self.calls = 0

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    def create(self, **kwargs):
        self.calls += 1

        response = httpx.Response(
            self.status_code,
            request=httpx.Request(
                "POST",
                "https://example.invalid/chat/completions",
            ),
        )

        raise APIStatusError(
            "模拟 API 错误",
            response=response,
            body=self.body,
        )


def main():
    cases = [
        (
            400,
            {"error": {"code": "context_length_exceeded"}},
            "context_overflow",
        ),
        (
            413,
            {"code": "context_window_exceeded"},
            "context_overflow",
        ),
        (
            400,
            {"error": {"code": "invalid_parameter"}},
            "api_error",
        ),
        (
            400,
            {"error": {"message": "some token-related error"}},
            "api_error",
        ),
        (
            500,
            {"error": {"code": "context_length_exceeded"}},
            "api_error",
        ),
    ]

    for status_code, body, expected_code in cases:
        client = ErrorClient(status_code, body)
        model = DeepSeekModel(client)

        try:
            model.generate(
                messages=[{"role": "user", "content": "测试"}],
                tools=[],
            )
        except ModelError as exc:
            assert exc.code == expected_code
        else:
            raise AssertionError("应返回模型错误")

        assert client.calls == 1

    print("识别已知上下文超限码，其他 API 错误不误分类：通过")
    print("错误识别不自动重试请求：通过")


if __name__ == "__main__":
    main()