import json
from contextlib import ExitStack
from pathlib import Path
from filelock import FileLock

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.bugfix_agent import BugfixAgent
from forgepilot.bugfix_delivery import export_bugfix_result
from forgepilot.bugfix_tasks import SavedBugfixTask, load_bugfix_task
from forgepilot.environments.bugfix_runtime import (
    start_bugfix_runtime,
)
from forgepilot.memory.recall import MemoryRecall
from forgepilot.memory.extraction import MemoryExtractionAgent
from forgepilot.memory.service import MemoryExtractionService
from forgepilot.tools.memory import build_memory_tools
from forgepilot.tools.memory_versions import build_memory_version_tools
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.control.controller import ContextController
from forgepilot.sessions.models import MessageEntry, SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.inspection import inspect_branch
from forgepilot.tools.artifacts import build_artifact_tools
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.control.stats import ContextStats
from forgepilot.sessions.collapse.selector import CollapseSelector
from forgepilot.sessions.collapse.summarizer import CollapseSummarizer
from forgepilot.sessions.collapse.collapser import SessionCollapser
from forgepilot.sessions.collapse.background import BackgroundCollapse
from forgepilot.sessions.budget.summary_job import SummaryJobLimits
from forgepilot.skills.service import PreparedSkill, SkillService
from forgepilot.skills.task_state import (
    load_skill_state,
    save_skill_state,
)
from forgepilot.exceptions import ModelError
from forgepilot.skills.extraction_service import SkillExtractionService
from forgepilot.permissions.service import PermissionService
from forgepilot.permissions.audit import PermissionAuditLog
class BugfixService:
    def __init__(
        self,
        *,
        model,
        source: Path,
        tasks_root: Path,
        budget: ContextBudget,
        collapse_model=None,
        memory_store=None,
        memory_selector=None,
        memory_extraction_model=None,
        memory_extraction_budget: ContextBudget | None = None,
        image: str = "python:3.13-slim",
        max_steps: int = 30,
        skill_store=None,
        skill_selector=None,
        skill_extractor=None,
        permission_service: PermissionService | None = None,
    ):
        self.model = model
        self.source = source.resolve()
        self.tasks_root = tasks_root.resolve()
        self.budget = budget
        self.image = image
        self.max_steps = max_steps
        if permission_service is not None and not isinstance(permission_service, PermissionService):
            raise TypeError("permission_service 必须是 PermissionService")
        self.permission_service = permission_service

        if collapse_model is model:
            raise ValueError("后台 Collapse 必须使用独立的模型适配器实例")
        self.collapse_model = collapse_model

        if (memory_store is None) != (memory_selector is None):
            raise ValueError("记忆库和记忆选择器必须一起提供")
        self.memory_store = memory_store
        self.memory_selector = memory_selector
        if (memory_extraction_model is None) != (memory_extraction_budget is None):
            raise ValueError("记忆提取模型和预算必须一起提供")
        if memory_extraction_model is not None and memory_store is None:
            raise ValueError("记忆提取需要项目记忆库")
        self.memory_extraction_model = memory_extraction_model
        self.memory_extraction_budget = memory_extraction_budget

        if max_steps <= 0:
            raise ValueError("max_steps 必须大于 0")
        if (skill_store is None) != (skill_selector is None):
            raise ValueError("Skill 库和选择器必须一起提供")

        self.skill_store = skill_store
        self.skill_selector = skill_selector
        if skill_extractor is not None and skill_store is None:
            raise ValueError("Skill 自动提取需要项目 Skill 库")

        self.skill_extractor = skill_extractor

    def run(
            self,
            ticket: str,
            *,
            skill_name: str | None = None,
    ) -> dict:
        if not ticket.strip():
            raise ValueError("Bug Ticket 不能为空")

        return self._execute(
            ticket,
            skill_name=skill_name,
        )

    def resume(self, task_id: str, *, recover_unknown: bool = False) -> dict:
        saved = load_bugfix_task(self.tasks_root, task_id)
        if saved.task.source_workspace != self.source:
            raise ValueError("任务不属于当前原工作区")
        return self._execute(saved.ticket, saved=saved, recover_unknown=recover_unknown)

    def _execute(
            self,
            ticket: str,
            *,
            saved: SavedBugfixTask | None = None,
            recover_unknown: bool = False,
            skill_name: str | None = None,
    ) -> dict:
        with ExitStack() as lifecycle:
            if saved is not None:
                lifecycle.enter_context(FileLock(str(saved.run_dir / "run.lock"), timeout=0))
                sessions = SessionStore(saved.run_dir / "session.jsonl")
                header = sessions.read()[0]
                if Path(header.cwd).resolve() != saved.task.path:
                    raise ValueError("会话工作区与任务副本不一致")
                branch = sessions.get_current_branch()
                state = inspect_branch(branch)
                if state == "needs_recovery":
                    if not recover_unknown:
                        return {
                            "status": "needs_recovery", "n_calls": 0,
                            "reason": "存在缺失工具结果；先检查任务文件，再用 --recover-unknown 标记为未知继续。",
                            "task_id": saved.task.id, "run_dir": str(saved.run_dir),
                            "task_workspace": str(saved.task.path), "delivery": {},
                        }
                    sessions.mark_pending_tools_unknown(expected_leaf_id=branch[-1].id)

            runtime = start_bugfix_runtime(
                source=self.source,
                tasks_root=self.tasks_root,
                image=saved.image if saved is not None else self.image,
                existing_task=saved.task if saved is not None else None,
            )
            lifecycle.callback(runtime.close)

            run_dir = self.tasks_root / "_runs" / runtime.task.id
            run_dir.mkdir(parents=True, exist_ok=True)

            metadata = {
                "task_id": runtime.task.id,
                "source_workspace": str(self.source),
                "task_workspace": str(runtime.task.path),
                "baseline_commit_id": runtime.task.baseline_commit_id,
                "ticket": ticket,
                "image": self.image,
            }
            if saved is None:
                lifecycle.enter_context(FileLock(str(run_dir / "run.lock"), timeout=0))
                (run_dir / "task.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                header = SessionHeader(
                    project_id="forgepilot-bugfix", owner_id="local-user", cwd=str(runtime.task.path),
                )
                sessions = SessionStore(run_dir / "session.jsonl")
                sessions.create(header)

            # 每个任务独立加载批准；不继承 coding 或其他任务的会话授权。
            base_permissions = self.permission_service or runtime.tools.permission_service
            runtime.tools.configure_permissions(service=base_permissions.for_session(
                path=run_dir / "permission-state.json",
                session_id=header.session_id,
                workspace=runtime.task.path,
            ))
            runtime.tools.configure_audit(PermissionAuditLog(
                run_dir / "permissions.jsonl",
                session_id=header.session_id,
                task_id=runtime.task.id,
            ))
            artifacts = ArtifactStore(run_dir / "artifacts")
            memory_recall = None
            if self.memory_store is not None:
                memory_recall = MemoryRecall(
                    memory_store=self.memory_store,
                    selector=self.memory_selector,
                    sessions=sessions,
                    state_path=run_dir / "memory-recall.json",
                )

            runtime.tools = runtime.tools.with_tools(
                build_artifact_tools(
                    session_store=sessions,
                    artifact_store=artifacts,
                )
            )
            if memory_recall is not None:
                runtime.tools = runtime.tools.with_tools(
                    build_memory_tools(
                        memory_store=self.memory_store,
                        consume_read=memory_recall.consume_tool_read,
                    ),
                    build_memory_version_tools(
                        memory_store=self.memory_store,
                        consume_read=memory_recall.consume_tool_read,
                    ),
                )

            skill_state_path = run_dir / "skill-state.json"

            prepared_skill = PreparedSkill(
                name=None,
                messages=[],
                registry=runtime.tools,
            )

            if skill_state_path.exists():
                prepared_skill = load_skill_state(
                    skill_state_path,
                    session_id=header.session_id,
                    tool_pool=runtime.tools,
                )

            elif saved is None and self.skill_store is not None:
                base_tools = {
                    "read_file",
                    "list_files",
                    "read_artifact",
                }

                if memory_recall is not None:
                    base_tools.update({
                        "read_memory",
                        "glob_memory",
                        "grep_memory",
                        "list_memory_versions",
                        "read_memory_version",
                    })

                skill_service = SkillService(
                    store=self.skill_store,
                    selector=self.skill_selector,
                    tool_pool=runtime.tools,
                    base_tools=base_tools,
                )

                prepared_skill = skill_service.prepare(
                    task=ticket,
                    requested_skill=skill_name,
                )

            elif skill_name is not None:
                raise ValueError("未配置 Skill 库，无法指定 Skill")

            runtime.tools = prepared_skill.registry

            if not skill_state_path.exists():
                save_skill_state(
                    skill_state_path,
                    session_id=header.session_id,
                    prepared=prepared_skill,
                )

            def request_prefix():
                memory_messages = (
                    memory_recall()
                    if memory_recall is not None
                    else []
                )

                return [
                    *prepared_skill.messages,
                    *memory_messages,
                ]
            context_stats = ContextStats()

            summarizer = ContextSummarizer(
                self.model,
                context_budget=self.budget,
                artifact_store=artifacts,
                stats=context_stats,
            )

            chunked_summarizer = ChunkedSummarizer(
                summarizer,
                max_chunks=8,
            )

            compactor = SessionCompactor(
                sessions,
                chunked_summarizer,
                stats=context_stats,
            )
            collapse_model = (
                self.collapse_model
                if self.collapse_model is not None
                else self.model
            )
            collapse_selector = CollapseSelector(
                collapse_model,
                context_budget=self.budget,
            )

            collapse_worker = ChunkedSummarizer(
                ContextSummarizer(
                    collapse_model,
                    context_budget=self.budget,
                    artifact_store=artifacts,
                    stats=context_stats,
                ),
                max_chunks=8,
            )

            collapser = SessionCollapser(
                store=sessions,
                selector=collapse_selector,
                summarizer=CollapseSummarizer(collapse_worker),
                model=collapse_model,
                tools=runtime.tools.definitions(),
                artifacts=artifacts,
                job_limits=SummaryJobLimits(
                    max_requests=9,
                    max_reserved_tokens=9 * (
                            self.budget.input_budget
                            + self.budget.max_output_tokens
                    ),
                    max_elapsed_seconds=180.0,
                ),
                minimum_savings_tokens=256,
            )

            background_collapse = None
            if self.collapse_model is not None:
                background_collapse = BackgroundCollapse(collapser)
                lifecycle.callback(background_collapse.close)

            controller = ContextController(
                model=self.model,
                tools=runtime.tools.definitions(),
                store=sessions,
                budget=self.budget,
                artifacts=artifacts,
                compactor=compactor,
                enable_snip=True,
                snip_ratio=0.7,
                keep_recent_groups=3,
                stats=context_stats,
                collapser=collapser,
                collapse_ratio=0.8,
                background_collapse=background_collapse,
                request_prefix_provider=request_prefix,
            )

            agent = BugfixAgent(
                model=self.model,
                runtime=runtime,
                max_steps=self.max_steps,
                session_store=sessions,
                artifact_store=artifacts,
                context_controller=controller,
            )

            print("任务 ID：", runtime.task.id)
            print("任务副本：", runtime.task.path)
            print("权限模式：", runtime.tools.permission_mode)
            print("权限审计日志：", run_dir / "permissions.jsonl")
            print("会话文件：", sessions.path)
            print("开始执行……", flush=True)

            if saved is None or inspect_branch(sessions.get_current_branch()) == "empty":
                result = agent.run_ticket(ticket)
            else:
                result = agent.resume()
            if background_collapse is not None:
                # 结束准备作业后再输出稳定的统计，不在关闭时发布候选。
                background_collapse.close()
            print("Snip 应用次数：", controller.stats.snip_applications)
            print("前缀压缩成功次数：", context_stats.compaction_successes)
            print("前缀压缩失败次数：", context_stats.compaction_failures)
            print("摘要模型请求次数：", context_stats.summary_requests)
            print("Collapse 尝试次数：", context_stats.collapse_attempts)
            print("Collapse 成功次数：", context_stats.collapse_successes)
            print("Collapse 跳过次数：", context_stats.collapse_skips)
            print("Collapse 失败次数：", context_stats.collapse_failures)
            print("工具原文目录：", artifacts.root)
            print("本次 Skill：", prepared_skill.name or "未使用")
            if memory_recall is not None:
                print("记忆召回状态：", memory_recall.last_status)

            (run_dir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            delivery = export_bugfix_result(
                task=runtime.task,
                # 报告使用原始执行记录，避免 Snip 截短验证证据。
                result={
                    **result,
                    "messages": [
                        entry.payload
                        for entry in sessions.get_current_branch()
                        if isinstance(entry, MessageEntry)
                    ],
                },
                output_dir=run_dir,
            )

            memory_extraction = {"status": "disabled"}
            if self.memory_extraction_model is not None:
                # 交付物先保存；记忆失败不会改变修复结果。
                memory_service = MemoryExtractionService(
                    sessions=sessions,
                    memory_store=self.memory_store,
                    agent=MemoryExtractionAgent(
                        model=self.memory_extraction_model,
                        memory_store=self.memory_store,
                        context_budget=self.memory_extraction_budget,
                    ),
                )
                if result["status"] == "completed":
                    memory_extraction = memory_service.process_completed_turn()
                else:
                    memory_extraction = {"status": "not_ready"}
                (run_dir / "memory-result.json").write_text(
                    json.dumps(memory_extraction, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print("长期记忆提取：", memory_extraction["status"])
                if memory_extraction["status"] == "failed":
                    print("长期记忆提取失败：", memory_extraction["reason"])

            skill_extraction = {"status": "disabled"}

            if self.skill_extractor is not None:
                if result["status"] != "completed":
                    skill_extraction = {"status": "not_ready"}

                else:
                    try:
                        skill_service = SkillExtractionService(
                            sessions=sessions,
                            skill_store=self.skill_store,
                            extractor=self.skill_extractor,
                        )

                        allowed_tools = {
                            item["name"]
                            for item in prepared_skill.registry.definitions()
                        }

                        print(
                            "Skill：检查本次修复流程是否值得提取……",
                            flush=True,
                        )

                        skill_extraction = (
                            skill_service.process_completed_task(
                                allowed_tools=allowed_tools,
                            )
                        )

                        (run_dir / "skill-result.json").write_text(
                            json.dumps(
                                skill_extraction,
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )

                    except (ModelError, ValueError, RuntimeError, OSError) as exc:
                        skill_extraction = {
                            "status": "failed",
                            "reason": str(exc),
                        }

                print("Skill 自动提取：", skill_extraction["status"])

                if skill_extraction["status"] in {"saved", "updated"}:
                    label = (
                        "新增 Skill："
                        if skill_extraction["status"] == "saved"
                        else "更新 Skill："
                    )

                    print(label, skill_extraction["name"])
                    print("保存位置：", skill_extraction["path"])

                elif skill_extraction["status"] in {
                    "failed",
                    "no_skill",
                    "skipped",
                    "unchanged",
                }:
                    print("Skill：", skill_extraction["reason"])
            return {
                **result,
                "memory_extraction": memory_extraction,
                "skill_extraction": skill_extraction,
                "task_id": runtime.task.id,
                "run_dir": str(run_dir),
                "task_workspace": str(runtime.task.path),
                "delivery": {
                    name: str(path)
                    for name, path in delivery.items()
                },
            }
