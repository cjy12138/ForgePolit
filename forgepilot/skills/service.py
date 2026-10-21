from dataclasses import dataclass

from forgepilot.skills.loader import SkillLoader
from forgepilot.skills.selector import SkillSelector
from forgepilot.skills.store import SkillStore
from forgepilot.tool_registry import ToolRegistry


@dataclass(frozen=True)
class PreparedSkill:
    name: str | None
    messages: list[dict[str, str]]
    registry: ToolRegistry

class SkillService:
    def __init__(
        self,
        *,
        store: SkillStore,
        selector: SkillSelector,
        tool_pool: ToolRegistry,
        base_tools: set[str],
    ):
        self.store = store
        self.selector = selector
        self.tool_pool = tool_pool

        self.loader = SkillLoader(
            store=store,
            tool_pool=tool_pool,
            base_tools=base_tools,
        )

    def prepare(
        self,
        *,
        task: str,
        requested_skill: str | None = None,
    ) -> PreparedSkill:
        if not task.strip():
            raise ValueError("任务不能为空")

        if requested_skill is not None:
            # 手动指定时直接加载，依赖不满足则明确报错。
            name = requested_skill
        else:
            available_names = {
                item["name"]
                for item in self.tool_pool.definitions()
            }

            candidates = [
                metadata
                for metadata in self.store.list_metadata()
                if set(metadata.tools) <= available_names
            ]

            name = self.selector.select(
                task=task,
                candidates=candidates,
            )

        if name is None:
            return PreparedSkill(
                name=None,
                messages=[],
                registry=self.tool_pool,
            )

        loaded = self.loader.load(name)

        return PreparedSkill(
            name=loaded.name,
            messages=loaded.messages,
            registry=loaded.registry,
        )