"""单次 Agent 执行中的重复调用保护，不承担幂等或中断恢复。"""
import hashlib
import json


class RepeatedCallGuard:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_key = None
        self.count = 0

    def key(self, name, arguments, context):
        payload = json.dumps(
            [str(context.workspace.resolve()), context.task, name, arguments],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def blocked(self, key):
        # 允许首次执行和一次重试；第三次连续相同调用开始拒绝。
        return key == self.last_key and self.count >= 2

    def record(self, key):
        self.count = self.count + 1 if key == self.last_key else 1
        self.last_key = key
