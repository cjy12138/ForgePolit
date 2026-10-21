import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class ForkSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    # 保存为字符串，防止外部修改嵌套的消息或工具定义。
    request_json: str
    messages_json: str
    sha256: str

    @classmethod
    def capture(cls, *, request: dict, messages: list[dict]) -> "ForkSnapshot":
        request_json = encode(request)
        messages_json = encode(messages)
        digest = hashlib.sha256(encode([request_json, messages_json]).encode("utf-8")).hexdigest()
        return cls(request_json=request_json, messages_json=messages_json, sha256=digest)

    @model_validator(mode="after")
    def check_contents(self):
        if not isinstance(json.loads(self.request_json), dict):
            raise ValueError("请求快照必须是对象")
        messages = json.loads(self.messages_json)
        if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
            raise ValueError("消息快照必须是消息列表")
        digest = hashlib.sha256(encode([self.request_json, self.messages_json]).encode("utf-8")).hexdigest()
        if digest != self.sha256:
            raise ValueError("Fork 快照指纹不匹配")
        return self

    def request(self) -> dict:
        return json.loads(self.request_json)

    def messages(self) -> list[dict]:
        return json.loads(self.messages_json)
