from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.models.deepseek import DeepSeekModel
from dataclasses import asdict
from time import monotonic
from filelock import FileLock
from filelock import Timeout
from forgepilot.fork.snapshot import encode


class DeepSeekForkModel(DeepSeekModel):
    """沿用父模型配置，按子任务实际工具与消息构建请求。"""

    def __init__(self, *, client, snapshot: ForkSnapshot, usage_path=None, before_request=None):
        self.snapshot = snapshot
        self.usage_path = usage_path
        self.before_request = before_request
        request = snapshot.request()
        super().__init__(client, model_name=request["model"], max_output_tokens=request["max_tokens"])

    def generate(self, messages, tools):
        if self.before_request is not None:
            self.before_request()
        started = monotonic()
        status = "failed"
        try:
            result = super().generate(messages, tools)
            status = "completed"
            return result
        finally:
            if self.usage_path is not None:
                record = {"model": self.model_name, "status": status,
                          "elapsed_ms": round((monotonic() - started) * 1000),
                          "usage": asdict(self.last_usage) if self.last_usage else None}
                try:
                    with FileLock(str(self.usage_path) + ".lock", timeout=5):
                        with self.usage_path.open("a", encoding="utf-8") as file:
                            file.write(encode(record) + "\n")
                except (OSError, Timeout) as exc:
                    print(f"Fork 用量日志保存失败：{type(exc).__name__}")

    def prepare_request(self, messages, tools):
        inherited = self.snapshot.messages()
        request = self.snapshot.request()
        if messages[:len(inherited)] == inherited:
            request["messages"] = [
                *request["messages"],
                *self._convert_messages(messages[len(inherited):]),
            ]
        else:
            # 子会话压缩或独立加载 Skill 后，以当前请求视图为准。
            request["messages"] = self._convert_messages(messages)
        request["tools"] = self._convert_tools(tools)
        return request
