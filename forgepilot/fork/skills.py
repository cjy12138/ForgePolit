from filelock import FileLock

from forgepilot.agent import Agent
from forgepilot.skills.service import PreparedSkill, SkillService
from forgepilot.skills.task_state import load_skill_state, save_skill_state
from forgepilot.skills.loader import SkillLoader


class ForkSkillAgent(Agent):
    def __init__(self, *args, skill_messages, **kwargs):
        self.skill_messages = [dict(message) for message in skill_messages]
        super().__init__(*args, **kwargs)

    def build_context_view(self, entries):
        if self.context_controller is not None:
            # Controller.prepare 在预算估算和发送前统一加入 Skill 指令。
            return super().build_context_view(entries)
        return [*self.skill_messages, *super().build_context_view(entries)]


def prepare_fork_skill(*, session, task, tool_pool, base_tools, store, selector, preloaded=()):
    path = session.path.parent / "skill-state.json"
    header, _ = session.read()
    with FileLock(str(path) + ".lock", timeout=5):
        if path.exists():
            prepared = load_skill_state(path, session_id=header.session_id, tool_pool=tool_pool)
        else:
            if preloaded:
                loader = SkillLoader(store=store, tool_pool=tool_pool, base_tools=base_tools)
                names, messages = set(base_tools), []
                for name in preloaded:
                    loaded = loader.load(name)
                    names.update(item["name"] for item in loaded.registry.definitions())
                    messages.extend(loaded.messages)
                prepared = PreparedSkill(name=",".join(preloaded), messages=messages,
                                         registry=tool_pool.select_tools(names))
            else:
                service = SkillService(
                    store=store, selector=selector, tool_pool=tool_pool, base_tools=base_tools,
                )
                prepared = service.prepare(task=task.task.instruction)
            if prepared.name is None:
                # 现有 SkillService 未选择时返回完整工具池；子任务只保留基础工具。
                prepared = PreparedSkill(name=None, messages=[], registry=tool_pool.select_tools(base_tools))
            save_skill_state(path, session_id=header.session_id, prepared=prepared)
        names = {item["name"] for item in prepared.registry.definitions()}
        if not base_tools.issubset(names) or "fork_tasks" in names:
            raise ValueError("子任务 Skill 工具配置不合法")
        return prepared
