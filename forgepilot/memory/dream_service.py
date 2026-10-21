from filelock import Timeout

from forgepilot.exceptions import ModelError
from forgepilot.memory.dream_agent import AutoDreamAgent
from forgepilot.memory.dream_publication import publish_dream_proposal
from forgepilot.memory.dream_state import AutoDreamStateStore
from forgepilot.memory.store import MarkdownMemoryStore


class AutoDreamService:
    def __init__(
        self,
        *,
        memory_store: MarkdownMemoryStore,
        agent: AutoDreamAgent,
    ):
        if agent.memory_store is not memory_store:
            raise ValueError("整理 Agent 与服务必须使用同一记忆存储对象")

        self.memory_store = memory_store
        self.agent = agent
        self.state = AutoDreamStateStore(memory_store)

    def run_if_due(
        self,
        *,
        now: float | None = None,
    ) -> dict:
        try:
            trigger = self.state.prepare_if_due(now=now)

            if trigger["status"] != "ready":
                return trigger

            activity = dict(trigger["activity"])

            # 模型调用期间不持有记忆写锁。
            proposal = self.agent.prepare()

            read_versions = dict(self.agent.read_versions)
            fully_read = set(self.agent.fully_read)

            # 发布与水位更新使用同一个写锁。
            with self.memory_store.write_lock():
                result = publish_dream_proposal(
                    proposal,
                    memory_store=self.memory_store,
                    read_versions=read_versions,
                    fully_read=fully_read,
                )

                # published 或 no_changes 都表示本次检查正常完成。
                self.state.mark_success(
                    activity,
                    now=now,
                )

            return {
                **result,
                "new_sessions": trigger["new_sessions"],
            }

        except Timeout:
            return {"status": "busy"}

        except (ModelError, ValueError, OSError) as exc:
            return {
                "status": "failed",
                "reason": str(exc),
            }