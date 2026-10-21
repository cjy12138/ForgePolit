import json
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.sessions.budget.token_estimator import (
    estimate_request_tokens,
)
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from forgepilot.exceptions import ModelError
from forgepilot.models.usage import (
    ModelUsage,
    RequestTokenMeasurement,
    normalize_usage,
)
from forgepilot.models.token_calibration import TokenEstimateCalibrator

CONTEXT_OVERFLOW_CODES = {
    "context_length_exceeded",
    "context_window_exceeded",
    "max_context_length_exceeded",
}


def is_context_overflow(exc: APIStatusError) -> bool:
    if exc.status_code not in (400, 413):
        return False

    body = exc.body

    if not isinstance(body, dict):
        return False

    error = body.get("error")

    if isinstance(error, dict):
        code = error.get("code")
    else:
        code = body.get("code")

    return (
        isinstance(code, str)
        and code.casefold() in CONTEXT_OVERFLOW_CODES
    )

class DeepSeekModel:
    def __init__(
        self,
        client,
        model_name="deepseek-v4-flash",
        *,
        max_output_tokens=1024,

    ):
        if (
                type(max_output_tokens) is not int
                or max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens 必须是正整数")

        self.client = client
        self.model_name = model_name
        self.max_output_tokens = max_output_tokens
        self.last_usage: ModelUsage | None = None
        self.last_fork_snapshot: ForkSnapshot | None = None
        self.last_measurement: RequestTokenMeasurement | None = None
        self.token_calibrator = TokenEstimateCalibrator(
            self.model_name
        )

    def generate(self, messages, tools):
        self.last_usage = None
        self.last_fork_snapshot = None
        self.last_measurement = None

        request = self.prepare_request(messages, tools)
        snapshot = ForkSnapshot.capture(request=request, messages=messages)

        estimated_input = estimate_request_tokens(request)

        self.last_measurement = RequestTokenMeasurement(
            model_name=self.model_name,
            estimated_input_tokens=estimated_input,
            usage=None,
        )

        try:
            response = self.client.chat.completions.create(
                **request
            )
        except AuthenticationError as exc:
            raise ModelError(
                "authentication_error",
                "模型 API 认证失败，请检查密钥。",
            ) from exc
        except RateLimitError as exc:
            raise ModelError(
                "rate_limit",
                "模型 API 返回限流或额度相关错误。",
            ) from exc
        except APITimeoutError as exc:
            raise ModelError(
                "timeout",
                "模型请求超时。",
            ) from exc
        except APIConnectionError as exc:
            raise ModelError(
                "connection_error",
                "无法连接模型 API。",
            ) from exc
        except APIStatusError as exc:
            if is_context_overflow(exc):
                raise ModelError(
                    "context_overflow",
                    "服务端报告上下文超限，本次模型请求未得到有效响应。",
                ) from exc

            raise ModelError(
                "api_error",
                f"模型 API 返回 HTTP {exc.status_code}。",
            ) from exc

        self.last_usage = normalize_usage(
            getattr(response, "usage", None)
        )
        self.last_measurement = RequestTokenMeasurement(
            model_name=self.model_name,
            estimated_input_tokens=estimated_input,
            usage=self.last_usage,
        )
        self.token_calibrator.observe(self.last_measurement)

        if not response.choices:
            raise ModelError(
                "invalid_response",
                "模型 API 返回了空 choices。",
            )

        choice  =response.choices[0]
        message = choice.message

        if choice.finish_reason not in ("stop","tool_calls"):
            raise ModelError(
                "unexpected_finish",
                f"模型未正常完成响应：{choice.finish_reason}",
            )

        if message.tool_calls:
            calls = []
            try:
                for call in message.tool_calls:
                    calls.append({
                        "type": "tool_call",
                        "id": call.id,
                        "name": call.function.name,
                        "arguments": json.loads(call.function.arguments),
                    })
            except json.JSONDecodeError as exc:
                raise ModelError(
                    "invalid_response",
                    "模型返回的工具参数不是合法 JSON。",
                ) from exc

            if len(calls) == 1:
                self.last_fork_snapshot = snapshot
                return calls[0]
            self.last_fork_snapshot = snapshot
            return {"type": "tool_batch", "calls": calls}

        self.last_fork_snapshot = snapshot
        return {
            "type": "final",
            "content": message.content or "",
        }


    def _convert_message(self,message):
        role = message["role"]

        if role in ("system","user"):
            return {
                "role": role,
                "content": message["content"],
            }

        if role == "assistant":
            if message["type"] == "tool_batch":
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(
                                    call["arguments"], ensure_ascii=False,
                                ),
                            },
                        }
                        for call in message["calls"]
                    ],
                }
            if message["type"] == "final":
                return {
                    "role": "assistant",
                    "content": message["content"],
                }

            if message["type"] == "tool_call":
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": message["id"],
                            "type": "function",
                            "function": {
                                "name": message["name"],
                                "arguments": json.dumps(
                                    message["arguments"],
                                    ensure_ascii=False,
                                ),
                            }
                        }
                    ],
                }

        if role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message["tool_call_id"],
                "content": json.dumps(
                    {
                        "ok": not message["is_error"],
                        "exit_code": message.get("exit_code"),
                        "output": message["content"],
                    },
                    ensure_ascii=False,
                ),
            }

        raise ValueError(f"不支持的内部消息格式：role={role}")

    def _convert_messages(self, messages):
        # 对每一条消息调用 _convert_message，返回转换后的列表。
        results = []
        for message in messages:
            results.append(self._convert_message(message))
        return results

    def _convert_tools(self,tools):
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            }
            for tool in tools
        ]

    def prepare_request(self, messages, tools):
        return {
            "model": self.model_name,
            "messages": self._convert_messages(messages),
            "tools": self._convert_tools(tools),
            "max_tokens": self.max_output_tokens,
            "stream": False,
            "extra_body": {
                "thinking": {"type": "disabled"},
            },
        }

    def estimate_input_tokens(self, messages, tools):
        request = self.prepare_request(messages, tools)
        raw_estimate = estimate_request_tokens(request)

        return self.token_calibrator.apply(raw_estimate)
