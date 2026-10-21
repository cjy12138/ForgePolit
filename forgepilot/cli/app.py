from forgepilot.paths import PROJECT_ROOT
from forgepilot.paths import ensure_demo_workspace
import os
from contextlib import ExitStack
from pathlib import Path
import json
from dotenv import load_dotenv
from openai import OpenAI
from forgepilot.memory.scope import project_memory_root
from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.extended import build_extended_tools
from forgepilot.tools.fork import build_fork_tools
from forgepilot.fork.definitions import AgentDefinitionStore
from forgepilot.fork.dispatch import ForkDispatcher
from forgepilot.fork.executor import ForkExecutor
from forgepilot.fork.factory import ForkAgentFactory
from forgepilot.fork.service import ForkService
from forgepilot.fork.store import ForkJobStore
from forgepilot.tools.search import build_search_tools
from forgepilot.tools.edit import build_edit_tools
from forgepilot.tools.commands import build_command_tools
from forgepilot.harness import Harness
from forgepilot.task_interpreter import TaskInterpreter
from forgepilot.tasks import TaskRequest
from forgepilot.environments.factory import get_environment
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
import argparse
import re
from forgepilot.sessions.inspection import inspect_branch
from forgepilot.sessions.models import CompactionEntry
from forgepilot.artifacts.store import ArtifactStore
from forgepilot.tools.artifacts import build_artifact_tools
from forgepilot.sessions.integrity import check_session_integrity
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.control.controller import ContextController
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.control.stats import ContextStats
from forgepilot.sessions.control.circuit_state import CircuitStateStore
from functools import partial
from forgepilot.sessions.compaction.benefit import CompactionBenefitCheck
from forgepilot.sessions.compaction.token_planning import (
    plan_compaction_by_tokens,
)
from forgepilot.sessions.cache.profile import build_summary_cache_profile
from forgepilot.sessions.cache.candidates import SummaryCandidateCache
from forgepilot.sessions.cache.snapshot import SummarySnapshotStore
from forgepilot.sessions.files.budget import add_current_file_context
from forgepilot.sessions.budget.summary_job import SummaryJobLimits
from forgepilot.sessions.collapse.collapser import SessionCollapser
from forgepilot.sessions.collapse.background import BackgroundCollapse
from forgepilot.sessions.collapse.selector import CollapseSelector
from forgepilot.sessions.collapse.summarizer import CollapseSummarizer
from forgepilot.checkpoints.git import GitCheckpointStore
from forgepilot.checkpoints.service import SessionCheckpointService
from forgepilot.checkpoints.cli import (
    run_checkpoint_action,
    run_checkout_action,
)
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.memory.selector import MemorySelector
from forgepilot.memory.recall import MemoryRecall
from forgepilot.tools.memory import build_memory_tools
from forgepilot.tools.memory_versions import build_memory_version_tools
from forgepilot.memory.extraction import MemoryExtractionAgent
from forgepilot.memory.service import MemoryExtractionService
from forgepilot.memory.notes_agent import SessionNotesAgent
from forgepilot.memory.notes_store import SessionNotesStore
from forgepilot.memory.notes_service import SessionNotesService
from forgepilot.tools.session_notes import build_session_notes_tools
from forgepilot.memory.dream_agent import AutoDreamAgent
from forgepilot.memory.dream_service import AutoDreamService
from forgepilot.cli.input import read_task
from forgepilot.cli.turn import run_task_turn
from forgepilot.memory.background import BackgroundMemoryExtraction
from forgepilot.cli.turn import run_task_turn, recent_dialogue
from forgepilot.bugfix_service import BugfixService
from forgepilot.skills.catalog import KNOWN_TOOL_NAMES
from forgepilot.skills.store import SkillStore
from forgepilot.skills.selector import SkillSelector
from forgepilot.skills.service import PreparedSkill, SkillService
from forgepilot.sessions.models import MessageEntry
from forgepilot.skills.task_state import (
    load_skill_state,
    save_skill_state,
)
from forgepilot.exceptions import ModelError
from forgepilot.skills.extractor import SkillExtractor
from forgepilot.skills.extraction_service import SkillExtractionService
from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.models import PermissionSettings
from forgepilot.permissions.reviewer import SafetyReviewer
from forgepilot.permissions.service import PermissionService
from forgepilot.permissions.config import load_permission_settings
from forgepilot.permissions.cli_args import add_permission_rule_arguments
from forgepilot.permissions.audit import PermissionAuditLog
COMPACTION_FAILURE_LIMIT = 3
def print_session_list(
    sessions_dir: Path,
    workspace: Path,
) -> None:
    count = 0

    for path in sorted(sessions_dir.glob("*.jsonl")):
        try:
            store = SessionStore(path)
            header, _ = store.read()

            # 与恢复入口使用相同的项目范围。
            if (
                    header.project_id != "forgepilot-demo"
                    or header.owner_id != "local-user"
                    or Path(header.cwd).resolve() != workspace.resolve()
            ):
                continue

            if header.session_id != path.stem:
                raise ValueError("文件名与内部 session_id 不一致")

            branch = store.get_current_branch()
            state = inspect_branch(branch)

        except (OSError, ValueError) as exc:
            print(f"{path.name} | 无法读取：{exc}")
            continue

        print(
            f"{header.session_id} | "
            f"{state} | "
            f"{len(branch)} 条消息"
        )
        count += 1

    if count == 0:
        print("没有找到可展示的当前项目会话。")

def print_context_stats(stats: ContextStats) -> None:
    print("摘要模型请求次数：", stats.summary_requests)
    print("Snip 视图应用次数：", stats.snip_applications)
    print(
        "压缩作业：",
        f"尝试 {stats.compaction_attempts} 次，",
        f"成功 {stats.compaction_successes} 次，",
        f"主动停止 {stats.compaction_stops} 次，",
        f"失败 {stats.compaction_failures} 次。",
    )
    print("候选摘要缓存命中次数：", stats.summary_cache_hits)
    print(
        "Collapse：",
        f"尝试 {stats.collapse_attempts}，",
        f"成功 {stats.collapse_successes}，",
        f"跳过 {stats.collapse_skips}，",
        f"失败 {stats.collapse_failures}",
    )

def print_compaction_state(controller: ContextController) -> None:
    print(
        "连续压缩失败次数：",
        controller.consecutive_compaction_failures,
    )
    print(
        "自动压缩状态：",
        "已暂停" if controller.compaction_circuit_open else "可用",
    )
def print_model_usage(label, model):
    calibrator = model.token_calibrator

    print(
        f"{label}校准状态：",
        f"有效样本 {calibrator.samples} 个，",
        f"当前系数 {calibrator.factor:.3f}",
    )

    measurement = model.last_measurement

    if measurement is None:
        print(f"{label}：本进程尚无请求用量记录")
        return

    print(
        f"{label}最近一次请求原始输入估算：",
        measurement.estimated_input_tokens,
    )

    usage = measurement.usage

    if usage is None:
        print(f"{label}最近一次请求：未获得有效 usage")
        return

    print(
        f"{label}最近一次请求实际用量：",
        f"输入 {usage.prompt_tokens}，",
        f"输出 {usage.completion_tokens}，",
        f"总计 {usage.total_tokens if usage.total_tokens is not None else '未知'}",
    )

    ratio = measurement.input_ratio

    if ratio is not None:
        print(
            f"{label}最近一次实际输入 / 原始估算：",
            f"{ratio:.3f}",
        )
def main():
    parser = argparse.ArgumentParser(
        description="运行 ForgePilot 命令行编程 Agent",
    )

    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="打开指定会话，必要时恢复未完成任务，然后进入连续对话",
    )

    parser.add_argument(
        "--mark-unknown",
        action="store_true",
        help="将缺失的工具结果记录为未知，然后退出；需配合 --resume",
    )

    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="列出当前项目的本地会话，然后退出",
    )

    parser.add_argument(
        "--show-branch",
        action="store_true",
        help="显示当前分支节点，然后退出；需配合 --resume",
    )

    parser.add_argument(
        "--switch-to",
        metavar="NODE_ID",
        help="切换到指定历史节点，然后退出；需配合 --resume",
    )
    parser.add_argument(
        "--show-context-state",
        action="store_true",
        help="查看上下文压缩失败状态后退出；需配合 --resume",
    )

    parser.add_argument(
        "--reset-compaction",
        action="store_true",
        help="清零连续压缩失败次数后退出；需配合 --resume",
    )
    parser.add_argument(
        "--save-checkpoint",
        action="store_true",
        help="保存代码并关联当前节点，然后退出；需配合 --resume",
    )
    parser.add_argument(
        "--preview-checkpoint",
        action="store_true",
        help="预览恢复到当前分支检查点的差异；需配合 --resume",
    )
    parser.add_argument(
        "--restore-checkpoint",
        action="store_true",
        help="展示差异并确认恢复代码；需配合 --resume",
    )
    parser.add_argument(
        "--auto-checkpoints",
        action="store_true",
        help="执行任务时自动保存发生变化的代码检查点",
    )

    parser.add_argument(
        "--checkout",
        metavar="NODE_ID",
        help=(
            "预览并确认切换对话及恢复代码；"
            "需配合 --resume"
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        metavar="PATH",
        help="指定代码工作区；默认使用 demo_workspace",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "coding", "bugfix"],
        default="auto",
        help="任务模式；auto 由任务理解器建议",
    )
    parser.add_argument(
        "--permission-mode",
        choices=[
            "default",
            "accept_edits",
            "plan",
            "bypass",
            "auto",
        ],
        default="default",
        help="工具权限模式，与 coding/bugfix 任务模式分别配置",
    )

    parser.add_argument("--fork-mark-unknown", action="store_true",
                        help="恢复 Fork 前，将子会话缺失的工具结果标记为未知；需配合 --resume")
    add_permission_rule_arguments(parser)
    args = parser.parse_args()
    if args.fork_mark_unknown and args.resume is None:
        parser.error("--fork-mark-unknown 必须配合 --resume")
    local_actions = [
        args.mark_unknown,
        args.show_branch,
        args.switch_to is not None,
        args.show_context_state,
        args.reset_compaction,
        args.save_checkpoint,
        args.preview_checkpoint,
        args.restore_checkpoint,
        args.checkout is not None,
    ]
    if args.fork_mark_unknown and any(local_actions):
        parser.error("--fork-mark-unknown 不能与其他本地会话操作同时使用")
    if args.fork_mark_unknown and any(local_actions):
        parser.error("--fork-mark-unknown 不能与其他本地会话操作同时使用")

    if sum(local_actions) > 1:
        parser.error("每次只能执行一个本地会话操作")

    if any(local_actions) and args.resume is None:
        parser.error("会话操作必须配合 --resume 使用")

    if args.list_sessions and (
            args.resume is not None or any(local_actions)
    ):
        parser.error("--list-sessions 不能与其他会话操作同时使用")
    project_root = PROJECT_ROOT
    demo_workspace = (project_root / "demo_workspace").resolve()

    workspace = (
        args.workspace.expanduser().resolve()
        if args.workspace is not None
        else ensure_demo_workspace()
    )

    if not workspace.is_dir():
        parser.error("工作区不存在或不是目录")

    # 会话管理
    sessions_dir = project_root / ".forgepilot" / "sessions"
    if args.list_sessions:
        print_session_list(sessions_dir, workspace)
        return


    if args.resume is None:
        header = SessionHeader(
            project_id="forgepilot-demo",
            owner_id="local-user",
            cwd=str(workspace.resolve()),
        )

        session_path = sessions_dir / f"{header.session_id}.jsonl"

        store = SessionStore(session_path)
        store.create(header)

    else:
        # 当前生成的会话 ID 是 32 位小写十六进制字符串。
        if re.fullmatch(r"[0-9a-f]{32}", args.resume) is None:
            parser.error("会话 ID 必须是 32 位小写十六进制字符串")

        session_path = sessions_dir / f"{args.resume}.jsonl"

        if not session_path.is_file():
            parser.error("指定的会话文件不存在")

        store = SessionStore(session_path)
        header, _ = store.read()

        if header.session_id != args.resume:
            parser.error("会话文件名与内部 session_id 不一致")

        if header.project_id != "forgepilot-demo":
            parser.error("会话不属于当前演示项目")

        if header.owner_id != "local-user":
            parser.error("会话不属于当前本地用户")

        if Path(header.cwd).resolve() != workspace.resolve():
            parser.error("会话工作区与当前工作区不一致")

    print("会话 ID：", header.session_id)
    print("会话文件：", session_path)

    checkpoint_action = (
        "save" if args.save_checkpoint
        else "preview" if args.preview_checkpoint
        else "restore" if args.restore_checkpoint
        else None
    )

    if checkpoint_action is not None:
        try:
            run_checkpoint_action(
                checkpoint_action,
                sessions=store,
                workspace=workspace,
            )
        except (
            ValueError,
            RuntimeError,
            OSError,
        ) as exc:
            parser.error(str(exc))

        return

    if args.checkout is not None:
        try:
            run_checkout_action(
                args.checkout,
                sessions=store,
                workspace=workspace,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            parser.error(str(exc))

        return

    if args.show_branch:
        branch = store.get_current_branch()

        if not branch:
            print("当前分支没有消息。")
            return

        for entry in branch:
            if isinstance(entry, CompactionEntry):
                print(
                    f"{entry.id} | compaction | "
                    f"保留起点：{entry.first_kept_entry_id}"
                )
                continue

            message = entry.payload
            # 原有显示逻辑不变。
            role = message.get("role")
            kind = message.get("type", role)

            if role == "assistant" and kind == "tool_call":
                preview = f"调用工具：{message['name']}"

            elif role == "assistant" and kind == "tool_batch":
                names = [
                    call["name"]
                    for call in message["calls"]
                ]
                preview = "批量调用：" + ", ".join(names)

            elif role == "tool":
                preview = (
                    f"工具结果：{message['tool_call_id']}，"
                    f"is_error={message.get('is_error')}"
                )

            else:
                preview = str(message.get("content", ""))

            preview = " ".join(preview.split())[:100]

            print(f"{entry.id} | {role}/{kind} | {preview}")

        print("当前节点：", branch[-1].id)
        return

    if args.switch_to is not None:
        try:
            store.move_cursor(args.switch_to)
        except ValueError as exc:
            parser.error(str(exc))

        print("已切换到节点：", args.switch_to)
        print("当前仅切换了对话位置，工作区文件没有回滚。")
        print("尚未请求模型；再次使用 --resume 可从此位置继续。")
        return

    if args.mark_unknown:
        branch = store.get_current_branch()

        if not branch:
            print("当前会话没有消息，无需处理。")
            return

        marked = store.mark_pending_tools_unknown(
            expected_leaf_id=branch[-1].id,
        )

        if marked:
            print("已记录为执行结果未知：", ", ".join(marked))
            print("记录已保存；尚未请求模型或执行工具。")
            print("检查工作区状态后，可使用以下命令继续：")
            print(
                ".\\.venv\\Scripts\\python.exe -X utf8 "
                "-m forgepilot.cli.app "
                f"--resume {header.session_id}"
            )
        else:
            print("当前会话没有缺失的工具结果，无需处理。")

        return

    if args.show_context_state or args.reset_compaction:
        circuit_store = CircuitStateStore(
            store.path.with_suffix(".context.json"),
            header.session_id,
        )

        try:
            state = circuit_store.load()

            if args.reset_compaction:
                previous_count = state.consecutive_failures
                circuit_store.save(0)

                print(
                    "连续压缩失败次数已清零：",
                    f"{previous_count} → 0",
                )
                print("再次使用 --resume 后可以重新尝试自动压缩。")
                return

            is_open = (
                state.consecutive_failures
                >= COMPACTION_FAILURE_LIMIT
            )

            print("连续压缩失败次数：", state.consecutive_failures)
            print("熔断阈值：", COMPACTION_FAILURE_LIMIT)
            print(
                "自动压缩状态：",
                "已暂停" if is_open else "可用",
            )
            return

        except (ValueError, OSError) as exc:
            parser.error(f"上下文状态操作失败：{exc}")

    checkpoint_hook = None

    if args.auto_checkpoints:
        try:
            checkpoint_service = SessionCheckpointService(
                sessions=store,
                git=GitCheckpointStore(workspace),
            )

            checkpoint_hook = (
                checkpoint_service.save_if_changed
            )

        except (ValueError, RuntimeError, OSError) as exc:
            parser.error(
                f"无法启用自动检查点：{exc}"
            )

        print("自动代码检查点：已启用")

    load_dotenv(
        project_root / ".env",
        override=True,
        encoding="utf-8-sig",
    )

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")

    # 项目主动采用的教学预算，不代表模型官方上下文窗口。
    business_budget = ContextBudget(
        context_window=32000,
        max_output_tokens=2048,
        safety_margin=2000,
        compact_ratio=0.9,
    )

    summary_budget = ContextBudget(
        context_window=32000,
        max_output_tokens=4096,
        safety_margin=2000,
        compact_ratio=0.9,
    )

    selection_budget = ContextBudget(
        context_window=32000,
        max_output_tokens=512,
        safety_margin=2000,
    )
    permission_budget = ContextBudget(
        context_window=8192,
        max_output_tokens=512,
        safety_margin=512,
    )
    memory_extraction_budget = ContextBudget(
        context_window=32000,
        max_output_tokens=2048,
        safety_margin=2000,
    )

    model_name = os.environ.get(
        "DEEPSEEK_MODEL",
        "deepseek-v4-flash",
    )

    with OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        timeout=60.0,
        max_retries=0,
    ) as client, ExitStack() as lifecycle:
        model = DeepSeekModel(
            client=client,
            model_name=model_name,
            max_output_tokens=business_budget.max_output_tokens,
        )

        summary_model = DeepSeekModel(
            client=client,
            model_name=model_name,
            max_output_tokens=summary_budget.max_output_tokens,
        )
        selection_model = DeepSeekModel(
            client=client,
            model_name=model_name,
            max_output_tokens=selection_budget.max_output_tokens,
        )
        environment = get_environment({
            "environment_class": "local",
        })
        artifacts = ArtifactStore(
            store.path.parent / "artifacts" / store.path.stem
        )
        memory_root = project_memory_root(
            project_root=project_root,
            workspace=workspace,
            owner_id=header.owner_id,
            project_id=header.project_id,
        )

        memory_store = MarkdownMemoryStore(memory_root)

        scope = memory_root.relative_to(
            project_root / ".forgepilot" / "memory"
        )

        skill_store = SkillStore(
            project_root / ".forgepilot" / "skills" / scope,
            known_tools=KNOWN_TOOL_NAMES,
        )

        skill_selector = SkillSelector(
            DeepSeekModel(
                client=client,
                model_name=os.environ.get("SKILL_MODEL", model_name),
                max_output_tokens=selection_budget.max_output_tokens,
            ),
            context_budget=selection_budget,
        )
        skill_extraction_budget = ContextBudget(
            context_window=32000,
            max_output_tokens=2048,
            safety_margin=2000,
        )

        skill_extraction_model = DeepSeekModel(
            client=client,
            model_name=os.environ.get("SKILL_MODEL", model_name),
            max_output_tokens=skill_extraction_budget.max_output_tokens,
        )

        coding_skill_extraction = SkillExtractionService(
            sessions=store,
            skill_store=skill_store,
            extractor=SkillExtractor(
                model=skill_extraction_model,
                context_budget=skill_extraction_budget,
            ),
        )
        memory_extraction_model = DeepSeekModel(
            client=client,
            model_name=os.environ.get(
                "MEMORY_MODEL",
                model_name,
            ),
            max_output_tokens=(
                memory_extraction_budget.max_output_tokens
            ),
        )

        memory_extraction_agent = MemoryExtractionAgent(
            model=memory_extraction_model,
            memory_store=memory_store,
            context_budget=memory_extraction_budget,
            max_steps=6,
        )

        memory_service = MemoryExtractionService(
            sessions=store,
            memory_store=memory_store,
            agent=memory_extraction_agent,
        )

        background_memory = BackgroundMemoryExtraction(memory_service)
        lifecycle.callback(background_memory.close)

        dream_budget = ContextBudget(
            context_window=32000,
            max_output_tokens=4096,
            safety_margin=2000,
        )

        dream_model = DeepSeekModel(
            client=client,
            model_name=os.environ.get(
                "MEMORY_MODEL",
                model_name,
            ),
            max_output_tokens=dream_budget.max_output_tokens,
        )

        dream_service = AutoDreamService(
            memory_store=memory_store,
            agent=AutoDreamAgent(
                model=dream_model,
                memory_store=memory_store,
                context_budget=dream_budget,
                max_steps=12,
            ),
        )

        # 启动时检查当前记忆范围是否需要整理。
        process_autodream(dream_service)

        notes_model = DeepSeekModel(
            client=client,
            model_name=os.environ.get(
                "SESSION_NOTES_MODEL",
                os.environ.get("MEMORY_MODEL", model_name),
            ),
            max_output_tokens=(
                memory_extraction_budget.max_output_tokens
            ),
        )

        notes_store = SessionNotesStore(store)

        notes_service = SessionNotesService(
            sessions=store,
            notes_store=SessionNotesStore(store),
            agent=SessionNotesAgent(
                model=notes_model,
                context_budget=memory_extraction_budget,
            ),
        )
        memory_selection_model = DeepSeekModel(
            client=client,
            model_name=os.environ.get("MEMORY_MODEL", model_name),
            max_output_tokens=selection_budget.max_output_tokens,
        )
        memory_recall = MemoryRecall(
            memory_store=memory_store,
            selector=MemorySelector(
                memory_selection_model, context_budget=selection_budget,
            ),
            sessions=store,
            state_path=store.path.with_suffix(".memory-recall.json"),
        )
        permission_reviewer = None

        if args.permission_mode == "auto":
            permission_model = DeepSeekModel(
                client=client.with_options(
                    timeout=20.0,
                    max_retries=0,
                ),
                model_name=os.environ.get(
                    "SAFETY_MODEL",
                    model_name,
                ),
                max_output_tokens=permission_budget.max_output_tokens,
            )

            permission_reviewer = SafetyReviewer(
                permission_model,
                context_budget=permission_budget,
            )

        permission_service = PermissionService(
            PermissionEngine(
                load_permission_settings(
                    workspace=workspace,
                    mode=args.permission_mode,
                    cli_rules=args.permission_rules,
                )
            ),
            reviewer=permission_reviewer,
        )
        registry = ToolRegistry(
            build_file_tools(),
            build_extended_tools(),
            build_search_tools(),
            build_edit_tools(),
            build_command_tools(environment=environment),
            build_artifact_tools(
                session_store=store,
                artifact_store=artifacts,
            ),
            build_memory_tools(
                memory_store=memory_store,
                consume_read=memory_recall.consume_tool_read,
            ),
            build_memory_version_tools(
                memory_store=memory_store,
                consume_read=memory_recall.consume_tool_read,
            ),
            build_session_notes_tools(
                notes_store=notes_store,
            ),
        )
        registry.configure_permissions(
            service=permission_service.for_session(
                path=store.path.with_suffix(".permissions.json"),
                session_id=header.session_id,
                workspace=workspace,
            ),
        )
        coding_audit_path = store.path.parent / "audits" / header.session_id / "permissions.jsonl"
        registry.configure_audit(PermissionAuditLog(
            coding_audit_path, session_id=header.session_id,
        ))
        fork_root = store.path.parent / "forks" / header.session_id
        fork_jobs = ForkJobStore(fork_root)
        fork_definitions_root = workspace / ".forgepilot" / "agents"
        if not fork_definitions_root.is_dir():
            fork_definitions_root = project_root / "examples" / "agents"
        fork_definitions = AgentDefinitionStore(fork_definitions_root)
        fork_descriptions = []
        for definition_path in sorted(fork_definitions_root.glob("*.md")):
            definition = fork_definitions.read(definition_path.stem)
            fork_descriptions.append(f"- {definition.name}: {definition.description}")
        # 给子 Agent 提供完整的运行时工具池，不使用本轮主 Skill 筛选后的工具。
        # 主会话笔记读取器绑定父会话，不能直接提供给子任务。
        fork_pool = registry.select_tools({
            item["name"] for item in registry.definitions()
            if item["name"] != "read_session_notes"
        })
        fork_selector = SkillSelector(
            DeepSeekModel(
                client=client, model_name=os.environ.get("SKILL_MODEL", model_name),
                max_output_tokens=selection_budget.max_output_tokens,
            ),
            context_budget=selection_budget,
        )
        def build_child_memory_tools(child_session):
            child_recall = MemoryRecall(
                memory_store=memory_store, sessions=child_session,
                state_path=child_session.path.parent / "memory-recall.json",
                selector=MemorySelector(
                    DeepSeekModel(client=client, model_name=os.environ.get("MEMORY_MODEL", model_name),
                                  max_output_tokens=selection_budget.max_output_tokens),
                    context_budget=selection_budget,
                ),
            )
            return {
                **build_memory_tools(memory_store=memory_store, consume_read=child_recall.consume_tool_read),
                **build_memory_version_tools(memory_store=memory_store, consume_read=child_recall.consume_tool_read),
            }

        fork_factory = ForkAgentFactory(
            client=client, parent_tools=fork_pool,
            context_window=business_budget.context_window,
            safety_margin=business_budget.safety_margin,
            skill_store=skill_store, skill_selector=fork_selector,
            agent_store=fork_definitions,
            child_tool_builder=build_child_memory_tools, parent_artifacts=artifacts,
        )
        fork_dispatcher = ForkDispatcher(
            service=ForkService(jobs=fork_jobs, parent_session=store),
            executor=ForkExecutor(jobs=fork_jobs, create_agent=fork_factory),
            parent_model=model,
            recovery_permissions=registry.permission_service,
        )
        fork_tool_group = build_fork_tools(
            dispatch=fork_dispatcher, agent_descriptions="\n".join(fork_descriptions),
        )
        registry = registry.with_tools(fork_tool_group)
        coding_skill_service = SkillService(
            store=skill_store,
            selector=skill_selector,
            tool_pool=registry,
            base_tools={
                "fork_tasks",
                "read_file",
                "list_files",
                "read_artifact",
                "read_memory",
                "glob_memory",
                "grep_memory",
                "list_memory_versions",
                "read_memory_version",
                "read_session_notes",
            },
        )

        active_coding_skill = PreparedSkill(
            name=None,
            messages=[],
            registry=registry,
        )
        requested_skill = None

        coding_skill_states = (
                store.path.parent
                / "skill-states"
                / header.session_id
        )

        def save_coding_skill_state():
            branch = store.get_current_branch()
            task_entry = branch[-1]

            if (
                    not isinstance(task_entry, MessageEntry)
                    or task_entry.payload.get("role") != "user"
            ):
                raise ValueError("保存 Skill 时，当前节点必须是用户任务")

            coding_skill_states.mkdir(
                parents=True,
                exist_ok=True,
            )

            save_skill_state(
                coding_skill_states / f"{task_entry.id}.json",
                session_id=header.session_id,
                prepared=active_coding_skill,
            )

        def coding_request_prefix():
            return [
                *active_coding_skill.messages,
                *memory_recall(),
            ]

        context_stats = ContextStats()

        summarizer = ContextSummarizer(
            summary_model,
            context_budget=summary_budget,
            artifact_store=artifacts,
            stats=context_stats,
        )

        chunked_summarizer = ChunkedSummarizer(
            summarizer,
            max_chunks=8,
            job_limits=SummaryJobLimits(
                max_requests=8,
                max_reserved_tokens=96000,
                max_elapsed_seconds=180.0,
            ),
        )

        collapse_selector = CollapseSelector(
            selection_model,
            context_budget=selection_budget,
        )

        # Keep mutable model measurements and chunk state separate from prefix compaction.
        collapse_summary_model = DeepSeekModel(
            client=client,
            model_name=model_name,
            max_output_tokens=summary_budget.max_output_tokens,
        )
        collapse_chunked_summarizer = ChunkedSummarizer(
            ContextSummarizer(
                collapse_summary_model,
                context_budget=summary_budget,
                artifact_store=artifacts,
                stats=context_stats,
            ),
            max_chunks=8,
        )
        collapser = SessionCollapser(
            store=store,
            selector=collapse_selector,
            summarizer=CollapseSummarizer(
                collapse_chunked_summarizer
            ),
            model=model,
            tools=registry.definitions(),
            artifacts=artifacts,
            job_limits=SummaryJobLimits(
                max_requests=9,
                max_reserved_tokens=96000,
                max_elapsed_seconds=180.0,
            ),
            minimum_savings_tokens=256,
        )
        candidate_cache = SummaryCandidateCache(
            profile=build_summary_cache_profile(
                chunked_summarizer,
                provider_id="https://api.deepseek.com",
            ),
            max_entries=16,
            snapshot_store=SummarySnapshotStore(
                store.path.with_suffix(".summary-candidate.json")
            ),
        )
        token_plan_builder = partial(
            plan_compaction_by_tokens,
            model=model,
            tools=registry.definitions(),
            target_input_tokens=int(
                business_budget.compact_at * 0.8
            ),
            summary_reserve_tokens=(
                summary_budget.max_output_tokens
            ),
        )
        benefit_check = CompactionBenefitCheck(
            model=model,
            tools=registry.definitions(),
            max_input_tokens=business_budget.input_budget,
            minimum_savings_tokens=256,
            artifacts=artifacts,
        )

        compactor = SessionCompactor(
            store,
            chunked_summarizer,
            plan_builder=lambda branch: token_plan_builder(
                branch,
                collapse_records=store.get_current_collapse_records(),
            ),
            publication_check=benefit_check,
            candidate_cache=candidate_cache,
            stats=context_stats,
        )
        circuit_store = CircuitStateStore(
            store.path.with_suffix(".context.json"),
            header.session_id,
        )
        tool_context = ToolContext(workspace=workspace)

        file_restorer = partial(
            add_current_file_context,
            model=model,
            tools=registry.definitions(),
            context=tool_context,
            authorize_read=lambda target: target.is_relative_to(
                workspace.resolve()
            ),
            input_limit_tokens=business_budget.input_budget,
            max_files=4,
            max_chars_per_file=4000,
            max_total_chars=12000,
        )

        background_collapse = BackgroundCollapse(collapser)
        lifecycle.callback(background_collapse.close)

        controller = ContextController(
            model=model,
            tools=registry.definitions(),
            store=store,
            budget=business_budget,
            artifacts=artifacts,
            compactor=compactor,
            enable_snip=True,
            snip_ratio=0.7,
            keep_recent_groups=3,
            stats=context_stats,
            circuit_store=circuit_store,
            max_compaction_failures=COMPACTION_FAILURE_LIMIT,
            file_restorer=file_restorer,
            collapser=collapser,
            collapse_ratio=0.8,
            background_collapse=background_collapse,
            request_prefix_provider=coding_request_prefix,
        )

        def update_notes_with_status() -> dict:
            outcome = notes_service.update_if_due()
            status = outcome["status"]

            if status == "updated":
                print("会话笔记已更新：", outcome["path"])
            elif status == "failed":
                print("会话笔记更新失败：", outcome["reason"])
            elif status == "busy":
                print("会话笔记：写锁忙，本次跳过。")

            return outcome

        agent = Agent(
            model=model,
            tools=registry,
            context=tool_context,
            max_steps=10,
            session_store=store,
            artifact_store=artifacts,
            context_controller=controller,
            checkpoint_hook=checkpoint_hook,
            notes_hook=update_notes_with_status,
            task_started_hook=save_coding_skill_state,
        )

        def apply_coding_skill(prepared: PreparedSkill) -> None:
            nonlocal active_coding_skill

            # 旧会话保存的 Skill 工具名单可能早于 Fork 功能。
            if "fork_tasks" not in {item["name"] for item in prepared.registry.definitions()}:
                prepared = PreparedSkill(
                    name=prepared.name, messages=prepared.messages,
                    registry=prepared.registry.with_tools(fork_tool_group),
                )
            definitions = prepared.registry.definitions()

            agent.tools = prepared.registry
            agent.tool_definitions = definitions
            controller.tools = definitions
            active_coding_skill = prepared

            print(
                "本次 coding Skill：",
                prepared.name or "未使用",
            )

            print(
                "本次启用工具：",
                [item["name"] for item in definitions],
            )

        def prepare_coding(task_data: dict) -> None:
            prepared = coding_skill_service.prepare(
                task=json.dumps(
                    task_data,
                    ensure_ascii=False,
                    indent=2,
                ),
                requested_skill=requested_skill,
            )

            apply_coding_skill(prepared)

        def restore_coding_skill() -> None:
            task_entry = next(
                (
                    entry
                    for entry in reversed(store.get_current_branch())
                    if isinstance(entry, MessageEntry)
                       and entry.payload.get("role") == "user"
                ),
                None,
            )

            if task_entry is None:
                raise ValueError("当前分支没有可恢复的用户任务")

            path = coding_skill_states / f"{task_entry.id}.json"

            if not path.is_file():
                raise ValueError(
                    "当前任务没有 Skill 快照，不能自动确定原工具配置"
                )

            prepared = load_skill_state(
                path,
                session_id=header.session_id,
                tool_pool=registry,
            )

            apply_coding_skill(prepared)

        def extract_coding_skill_with_status() -> None:
            try:
                task_entry = next(
                    (
                        entry
                        for entry in reversed(store.get_current_branch())
                        if isinstance(entry, MessageEntry)
                           and entry.payload.get("role") == "user"
                    ),
                    None,
                )

                if task_entry is None:
                    return

                snapshot_path = (
                        coding_skill_states / f"{task_entry.id}.json"
                )

                # 旧任务或主会话里的 bugfix 结果没有 coding 快照。
                if not snapshot_path.is_file():
                    return

                prepared = load_skill_state(
                    snapshot_path,
                    session_id=header.session_id,
                    tool_pool=registry,
                )

                allowed_tools = {
                    item["name"]
                    for item in prepared.registry.definitions()
                }

                print("Skill：检查本次流程是否值得提取……", flush=True)

                outcome = coding_skill_extraction.process_completed_task(
                    allowed_tools=allowed_tools,
                )

                if outcome["status"] in {"saved", "updated"}:
                    label = (
                        "Skill 已自动保存："
                        if outcome["status"] == "saved"
                        else "Skill 已自动更新："
                    )

                    print(label, outcome["name"])
                    print("保存位置：", outcome["path"])

                elif outcome["status"] in {
                    "no_skill",
                    "skipped",
                    "unchanged",
                }:
                    print("Skill：", outcome["reason"])

            except (ModelError, ValueError, RuntimeError, OSError) as exc:
                print("Skill 自动提取失败：", exc)

        if args.resume is not None:
            state = inspect_branch(store.get_current_branch())

            if state == "needs_recovery":
                try:
                    restore_coding_skill()
                    recovered = fork_dispatcher.recover_pending(
                        tool_context, mark_unknown=args.fork_mark_unknown,
                    )
                    print("Fork 恢复完成：", ", ".join(recovered))
                    state = inspect_branch(store.get_current_branch())
                except (ValueError, ModelError, OSError) as exc:
                    print("无法自动恢复：", exc)
                    print("若为子工具结果未知，请检查现场后加 --fork-mark-unknown；非 Fork 调用使用 --mark-unknown。")
                    return

            if state == "ready":
                try:
                    restore_coding_skill()
                    result = agent.resume()
                except ValueError as exc:
                    print("无法恢复会话：", exc)
                    return

                print("恢复状态：", result["status"])
                print(result.get("answer", result.get("reason")))
                print("本次模型请求次数：", result["n_calls"])

                if result["status"] != "completed":
                    print("任务尚未完成，本次停止。")
                    return

            elif state == "completed":
                print("已打开会话，可以继续输入新任务。")

            elif state == "empty":
                print("当前会话尚无任务，可以开始输入。")

            # 完成分支也检查一次：
            # 上次退出时可能尚未完成长期记忆提取。
            if inspect_branch(store.get_current_branch()) == "completed":
                extract_coding_skill_with_status()
                memory_start = background_memory.start()

                if memory_start["status"] == "started":
                    print("长期记忆：已启动后台提取。")
                else:
                    print_memory_result(memory_start)


        bugfix_collapse_model = DeepSeekModel(
            client=client,
            model_name=model.model_name,
            max_output_tokens=model.max_output_tokens,
        )

        bugfix_memory_extraction_model = DeepSeekModel(
            client=client,
            model_name=os.environ.get("MEMORY_MODEL", model.model_name),
            max_output_tokens=memory_extraction_budget.max_output_tokens,
        )

        bugfix_service = BugfixService(
            model=model,
            permission_service=permission_service,
            collapse_model=bugfix_collapse_model,
            memory_store=memory_store,
            memory_selector=memory_recall.selector,
            memory_extraction_model=bugfix_memory_extraction_model,
            memory_extraction_budget=memory_extraction_budget,
            source=workspace,
            tasks_root=Path.home() / ".forgepilot" / "bugfix-workspaces",
            budget=business_budget,
            max_steps=30,
            skill_store=skill_store,
            skill_selector=skill_selector,
            skill_extractor=SkillExtractor(
                model=skill_extraction_model,
                context_budget=skill_extraction_budget,
            ),
        )

        def run_bugfix(task_data: dict) -> dict:
            ticket = (
                    "原始需求中的要求和限制必须保留，"
                    "interpreted_goal 仅用于辅助理解。\n"
                    + json.dumps(task_data, ensure_ascii=False, indent=2)
            )

            result = bugfix_service.run(
                ticket,
                skill_name=requested_skill,
            )

            # 完整执行历史在任务会话中。
            # 任务返回后，才把结果接入主会话。
            store.append_message({
                "role": "user",
                "content": task_data["original_request"],
            })

            summary = {
                "event": "bugfix_task_result",
                "task_id": result["task_id"],
                "status": result["status"],
                "agent_report": result.get(
                    "answer",
                    result.get("reason", ""),
                ),
                "run_dir": result["run_dir"],
                "delivery": result["delivery"],
                "applied_to_source": False,
                "notice": (
                    "agent_report 是任务 Agent 的报告；"
                    "实际执行证据见运行记录，"
                    "completed 不等于独立验收通过。"
                ),
            }

            store.append_message({
                "role": "assistant",
                "type": "final",
                "content": json.dumps(
                    summary,
                    ensure_ascii=False,
                    indent=2,
                ),
            })

            return result

        harness = Harness(
            interpreter=TaskInterpreter(model),
            agent=agent,
            history_provider=lambda: recent_dialogue(store),
            bugfix_handler=run_bugfix,
            coding_prepare=prepare_coding,
        )
        current_mode = args.mode

        print("请输入任务，输入 /exit 退出。")
        print("使用 /mode auto、/mode coding、/mode bugfix 切换模式。")
        print("当前模式：", current_mode)
        print("权限模式：", args.permission_mode)
        print("权限审计日志：", coding_audit_path)
        print("子 Agent 定义目录：", fork_definitions_root)
        print("Fork 执行记录目录：", fork_root)
        print("当前工作区：", workspace.resolve())
        print(
            "使用 /skill list 查看 Skill，"
            "/skill 名称手动选择，"
            "/skill auto 恢复自动选择。"
        )

        while True:
            text = read_task()

            # 用户输入期间，上一轮记忆已经在后台提取。
            # 开始下一轮或退出前，完成发布。
            memory_outcome = background_memory.finish()
            print_memory_result(memory_outcome)

            if memory_outcome["status"] in {"published", "no_memories"}:
                process_autodream(dream_service)

            # /exit、Ctrl+C 或输入结束时，read_task() 返回 None。
            if text is None:
                break

            if text == "/skill" or text.startswith("/skill "):
                parts = text.split()
                # 后面的原有代码保留。

                if len(parts) == 1:
                    print(
                        "当前 Skill 选择：",
                        requested_skill or "自动选择",
                    )

                elif len(parts) != 2:
                    print(
                        "用法：/skill、/skill list、"
                        "/skill auto、/skill 名称"
                    )

                elif parts[1] == "list":
                    items = skill_store.list_metadata()

                    if not items:
                        print("当前项目没有 Skill。")

                    for metadata in items:
                        print(
                            f"{metadata.name}: "
                            f"{metadata.description}"
                        )
                        print(
                            "  工具依赖：",
                            ", ".join(metadata.tools) or "无",
                        )

                elif parts[1] == "auto":
                    requested_skill = None
                    print("后续新任务将自动选择 Skill。")

                else:
                    name = parts[1]

                    try:
                        skill_store.read(name)
                    except (ValueError, OSError) as exc:
                        print("无法选择 Skill：", exc)
                    else:
                        requested_skill = name
                        print("后续新任务使用 Skill：", name)
                        print("执行前会检查当前模式的工具依赖。")

                continue

            if text == "/mode" or text.startswith("/mode "):
                parts = text.split()

                if len(parts) == 1:
                    print("当前模式：", current_mode)

                elif len(parts) == 2 and parts[1] in {
                    "auto",
                    "coding",
                    "bugfix",
                }:
                    current_mode = parts[1]
                    print("已切换模式：", current_mode)

                else:
                    print("用法：/mode auto、/mode coding、/mode bugfix")

                continue

            # 上一轮没有完成时，不能直接开始新的用户任务。
            state = inspect_branch(store.get_current_branch())

            if state not in {"empty", "completed"}:
                print("当前会话存在未完成任务，请先用 --resume 恢复。")
                break

            result = run_task_turn(
                harness,
                text,
                mode=None if current_mode == "auto" else current_mode,
            )

            if result is None:
                continue

            if result.get("stage") == "interpretation":
                print("任务理解失败：", result["reason"])
                continue

            print("本轮执行模式：", result["mode"])
            print("状态：", result["status"])
            print(result.get("answer", result.get("reason")))
            print("本轮模型请求次数：", result["n_calls"])
            print("本轮执行消息数量：", len(result["messages"]))

            if result.get("mode") == "bugfix":
                if result.get("task_id"):
                    print("运行记录：", result["run_dir"])
                    print("修复补丁：", result["delivery"]["patch_path"])
                    print("运行报告：", result["delivery"]["report_path"])
                    print("修复尚未应用到原工作区。")

                    memory_start = background_memory.start()

                    if memory_start["status"] == "started":
                        print("长期记忆：已启动后台提取。")
                    else:
                        print_memory_result(memory_start)

                continue

            if result["status"] != "completed":
                print("本轮未完成，停止接收新任务。")
                print(
                    "恢复命令：",
                    ".\\.venv\\Scripts\\python.exe -X utf8 "
                    "-m forgepilot.cli.app "
                    f"--resume {header.session_id}",
                )
                break

            extract_coding_skill_with_status()
            memory_start = background_memory.start()

            if memory_start["status"] == "started":
                print("长期记忆：已启动后台提取。")
            else:
                print_memory_result(memory_start)

        background_collapse.close()
        print_context_stats(context_stats)

        print_compaction_state(controller)
        print_model_usage("业务与任务理解 API ", model)
        print_model_usage("摘要 API ", summary_model)
        print("记忆召回状态：", memory_recall.last_status)
        print_model_usage("记忆选择 API ", memory_selection_model)
        print_model_usage(
            "Collapse 范围选择 API ",
            selection_model,
        )
        print_model_usage(
            "记忆提取 API ",
            memory_extraction_model,
        )
        integrity = check_session_integrity(store, artifacts)

        print(
            "当前分支保存完整性检查：通过",
            f"消息 {integrity.message_count} 条，",
            f"压缩节点 {integrity.compaction_count} 个，",
            f"已校验产物 {integrity.artifact_count} 个。",
        )


def process_turn_memory(
    result: dict,
    service: MemoryExtractionService,
) -> None:
    if result["status"] != "completed":
        return

    print("长期记忆：检查本轮是否需要提取……", flush=True)

    memory_result = service.process_completed_turn()
    print_memory_result(memory_result)


def print_memory_result(memory_result: dict) -> None:
    status = memory_result["status"]

    if status == "published":
        if memory_result["filenames"]:
            print("长期记忆已保存：", ", ".join(memory_result["filenames"]))
        else:
            print("长期记忆：索引选择已更新。")
        if memory_result.get("unindexed_filenames"):
            print("已保存但未收录索引（仍可搜索）：", ", ".join(memory_result["unindexed_filenames"]))

    elif status == "no_memories":
        print("长期记忆：本轮没有新的可复用信息。")

    elif status == "already_processed":
        print("长期记忆：本轮已经处理，跳过。")

    elif status == "busy":
        print("长期记忆：写锁忙，本次未完成处理。")

    elif status == "not_ready":
        print("长期记忆：当前分支尚未满足提取条件。")

    elif status == "failed":
        print(
            "长期记忆处理失败：",
            memory_result["reason"],
        )


def process_autodream(service: AutoDreamService) -> None:
    outcome = service.run_if_due()
    status = outcome["status"]

    # 条件不足时保持安静。
    if status in {"too_soon", "not_enough_sessions"}:
        return

    if status == "published":
        print(
            "AutoDream 整理完成：",
            f"更新 {len(outcome['rewritten'])} 个主题，",
            f"合并删除 {len(outcome['deleted'])} 个主题。",
        )
        print("整理前备份：", outcome["backup_path"])
        print("MEMORY.md 收录主题数：", len(outcome["indexed_filenames"]))

    elif status == "no_changes":
        print("AutoDream：检查完成，无需修改。")

    elif status == "busy":
        print("AutoDream：写锁忙，本次跳过。")

    elif status == "failed":
        print("AutoDream 整理失败：", outcome["reason"])

    for conflict in outcome.get("conflicts", []):
        print(
            "记忆冲突待确认：",
            ", ".join(conflict["filenames"]),
        )
        print("需要确认：", conflict["question"])
if __name__ == "__main__":
    main()
