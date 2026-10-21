from pathlib import Path

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.fork.model import DeepSeekForkModel
from forgepilot.fork.models import ForkTaskState
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.permissions.audit import PermissionAuditLog
from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.service import PermissionService
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.store import SessionStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.fork.skills import ForkSkillAgent, prepare_fork_skill
from forgepilot.artifacts.store import ArtifactStore
from forgepilot.tools.artifacts import build_artifact_tools
from forgepilot.fork.context import build_fork_context


class ForkAgentFactory:
    def __init__(self, *, client, parent_tools: ToolRegistry,
                 context_window: int, safety_margin: int = 2000, max_steps: int = 8,
                 approved_modes: frozenset[str] = frozenset(),
                 skill_store=None, skill_selector=None, agent_store=None,
                 child_tool_builder=None, parent_artifacts=None):
        if type(max_steps) is not int or max_steps <= 0:
            raise ValueError("子任务请求次数上限必须是正整数")
        self.client = client
        self.parent_tools = parent_tools
        self.context_window = context_window
        self.safety_margin = safety_margin
        self.max_steps = max_steps
        # 只能由程序依据用户配置设置，不能来自模型工具参数。
        self.approved_modes = frozenset(approved_modes)
        if skill_selector is not None and skill_store is None:
            raise ValueError("配置 Skill 选择器时必须提供存储")
        self.skill_store = skill_store
        self.skill_selector = skill_selector
        self.agent_store = agent_store
        self.child_tool_builder = child_tool_builder
        self.parent_artifacts = parent_artifacts

    def __call__(self, session: SessionStore, snapshot: ForkSnapshot,
                 task: ForkTaskState) -> Agent:
        parent = self.parent_tools.permission_service
        current_mode = parent.engine.settings.mode
        mode = task.task.permission_mode or current_mode
        if current_mode == "plan" and mode != "plan":
            raise ValueError("plan 主任务不能派发更宽权限的子任务")
        if mode not in {current_mode, "plan"} and mode not in self.approved_modes:
            raise ValueError("子任务权限模式变更尚未获得用户授权")

        settings = parent.engine.settings.model_copy(deep=True)
        if parent.session_state is not None:
            # 子任务与主任务共享同一会话授权，恢复时加载磁盘中的最新批准。
            refreshed = PermissionEngine(settings)
            refreshed.replace_session_rules(parent.session_state.load())
            settings = refreshed.settings
        settings.mode = mode
        permissions = PermissionService(
            PermissionEngine(settings), reviewer=parent.reviewer,
            approval=parent.approval, parent_authorize=parent.parent_authorize,
            session_state=parent.session_state,
        )
        pool = self.parent_tools
        if self.child_tool_builder is not None:
            overrides = self.child_tool_builder(session)
            if "fork_tasks" in overrides:
                raise ValueError("子工具构建器不能提供递归派发")
            pool = pool._copy_permission_config(pool._new_registry({**pool._tools, **overrides}))
        available = {item["name"] for item in pool.definitions()} - {"fork_tasks"}
        names = set(task.task.base_tools)
        definition = None
        preloaded = list(task.task.skills)
        if task.task.agent_type is not None:
            if self.agent_store is None:
                raise ValueError("尚未配置子 Agent 定义目录")
            definition = self.agent_store.for_session(task.task.agent_type, session.path)
            configured = available if definition.tools is None else set(definition.tools)
            names = configured - set(definition.disallowedTools)
            available -= set(definition.disallowedTools)
            if definition.tools is not None:
                available &= configured
            preloaded = list(dict.fromkeys([*definition.skills, *preloaded]))
        if not names.issubset(available):
            raise ValueError("子任务请求了未提供的基础工具：" + ", ".join(sorted(names - available)))
        tools = pool.select_tools(names)
        skill_messages = []
        if preloaded and self.skill_store is None:
            raise ValueError("预加载 Skill 需要配置 Skill 存储")
        if self.skill_store is not None and (preloaded or self.skill_selector is not None):
            prepared = prepare_fork_skill(
                session=session, task=task,
                tool_pool=pool.select_tools(available), base_tools=names,
                store=self.skill_store, selector=self.skill_selector, preloaded=preloaded,
            )
            tools = prepared.registry
            skill_messages = prepared.messages
        if definition is not None and definition.prompt:
            skill_messages = [{"role": "system", "content": definition.prompt}, *skill_messages]
        artifacts = ArtifactStore(session.path.parent / "artifacts")
        if self.parent_artifacts is not None:
            from forgepilot.fork.artifacts import inherit_artifacts
            inherit_artifacts(snapshot, parent=self.parent_artifacts, child=artifacts)
        # 绑定本子会话的产物读取器，绝不能沿用父会话 handler。
        snip_enabled = any(item["name"] == "read_artifact" for item in tools.definitions())
        names = {item["name"] for item in tools.definitions()} - {"read_artifact"}
        tools = tools.select_tools(names)
        if snip_enabled:
            # 替换为当前子会话的 handler，并保留现有工具与权限配置。
            tools = tools._copy_permission_config(tools._new_registry({
                **tools._tools,
                **build_artifact_tools(session_store=session, artifact_store=artifacts),
            }))
        tools.configure_permissions(service=permissions)
        header, _ = session.read()
        tools.configure_audit(PermissionAuditLog(
            session.path.parent / "permissions.jsonl", session_id=header.session_id,
            task_id=task.id,
        ))
        model = DeepSeekForkModel(client=self.client, snapshot=snapshot)
        model.usage_path = session.path.parent / "usage.jsonl"
        budget = ContextBudget(
            context_window=self.context_window, max_output_tokens=model.max_output_tokens,
            safety_margin=self.safety_margin,
        )
        controller = build_fork_context(
            client=self.client, model=model, tools=tools.definitions(), session=session,
            artifacts=artifacts, budget=budget, skill_messages=skill_messages,
            enable_snip=snip_enabled,
        )
        return ForkSkillAgent(
            model, tools, ToolContext(workspace=Path(header.cwd)),
            max_steps=self.max_steps, session_store=session,
            skill_messages=skill_messages,
            artifact_store=artifacts, context_controller=controller,
        )
