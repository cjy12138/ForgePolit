from forgepilot.paths import PROJECT_ROOT
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from forgepilot.permissions.config import load_permission_settings
from forgepilot.permissions.cli_args import add_permission_rule_arguments
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.sessions.budget.context_budget import ContextBudget

from forgepilot.bugfix_service import BugfixService
from forgepilot.bugfix_tasks import load_bugfix_task
from filelock import Timeout
from forgepilot.memory.scope import project_memory_root
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.memory.selector import MemorySelector
from forgepilot.skills.catalog import KNOWN_TOOL_NAMES
from forgepilot.skills.store import SkillStore
from forgepilot.skills.selector import SkillSelector
from forgepilot.skills.extractor import SkillExtractor
from forgepilot.permissions.engine import PermissionEngine
from forgepilot.permissions.models import PermissionSettings
from forgepilot.permissions.reviewer import SafetyReviewer
from forgepilot.permissions.service import PermissionService
def main() -> int:
    parser = argparse.ArgumentParser(
        description="在独立工作区和 Docker 中执行 Bug Ticket",
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--ticket")
    parser.add_argument("--resume", metavar="TASK_ID", help="恢复已有任务，复用原任务副本和基线")
    parser.add_argument("--recover-unknown", action="store_true", help="将缺失工具结果标记为未知后继续，不重放旧调用")
    parser.add_argument("--image", default="python:3.13-slim")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument(
        "--permission-mode",
        choices=["default", "accept_edits", "plan", "bypass", "auto"],
        default="default",
        help="工具权限模式；恢复任务时也采用本次启动指定的模式",
    )
    parser.add_argument(
        "--tasks-root",
        type=Path,
        default=Path.home() / ".forgepilot" / "bugfix-workspaces",
    )
    parser.add_argument(
        "--skill",
        metavar="NAME",
        help="手动指定 Skill；不指定时自动选择",
    )
    add_permission_rule_arguments(parser)
    args = parser.parse_args()

    if args.resume:
        if args.workspace is not None or args.ticket is not None:
            parser.error("--resume 不能与 --workspace 或 --ticket 同时使用")

        if args.skill is not None:
            parser.error("--resume 使用任务保存的 Skill，不能重新指定 --skill")
    elif args.workspace is None or not args.ticket or not args.ticket.strip():
        parser.error("新任务需要 --workspace 和非空 --ticket")
    if args.recover_unknown and not args.resume:
        parser.error("--recover-unknown 必须配合 --resume")

    if args.max_steps <= 0:
        parser.error("--max-steps 必须大于 0")

    project_root = PROJECT_ROOT
    tasks_root = args.tasks_root.expanduser().resolve()
    saved = load_bugfix_task(tasks_root, args.resume) if args.resume else None
    source = saved.task.source_workspace if saved else args.workspace.expanduser().resolve()

    load_dotenv(
        project_root / ".env",
        override=True,
        encoding="utf-8-sig",
    )

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        parser.error("缺少 DEEPSEEK_API_KEY")

    budget = ContextBudget(
        context_window=32000,
        max_output_tokens=2048,
        safety_margin=2000,
    )
    memory_root = project_memory_root(
        project_root=project_root,
        workspace=source,
    )

    memory_store = MarkdownMemoryStore(memory_root)

    scope = memory_root.relative_to(
        project_root / ".forgepilot" / "memory"
    )

    skill_store = SkillStore(
        project_root / ".forgepilot" / "skills" / scope,
        known_tools=KNOWN_TOOL_NAMES,
    )
    memory_selection_budget = ContextBudget(
        context_window=32000, max_output_tokens=512, safety_margin=2000,
    )

    with OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=60.0,
            max_retries=0,
    ) as client:
        model = DeepSeekModel(
            client=client,
            model_name=os.environ.get(
                "DEEPSEEK_MODEL",
                "deepseek-v4-flash",
            ),
            max_output_tokens=budget.max_output_tokens,
        )

        permission_reviewer = None
        if args.permission_mode == "auto":
            permission_budget = ContextBudget(
                context_window=8192,
                max_output_tokens=512,
                safety_margin=512,
            )
            permission_model = DeepSeekModel(
                client=client.with_options(timeout=20.0, max_retries=0),
                model_name=os.environ.get("SAFETY_MODEL", model.model_name),
                max_output_tokens=permission_budget.max_output_tokens,
            )
            permission_reviewer = SafetyReviewer(
                permission_model,
                context_budget=permission_budget,
            )

        permission_service = PermissionService(
            PermissionEngine(
                load_permission_settings(
                    workspace=source,
                    mode=args.permission_mode,
                    cli_rules=args.permission_rules,
                )
            ),
            reviewer=permission_reviewer,
        )

        skill_selection_budget = ContextBudget(
            context_window=32000,
            max_output_tokens=512,
            safety_margin=2000,
        )

        skill_selector = SkillSelector(
            DeepSeekModel(
                client=client,
                model_name=os.environ.get(
                    "SKILL_MODEL",
                    model.model_name,
                ),
                max_output_tokens=skill_selection_budget.max_output_tokens,
            ),
            context_budget=skill_selection_budget,
        )

        bugfix_collapse_model = DeepSeekModel(
            client=client,
            model_name=model.model_name,
            max_output_tokens=budget.max_output_tokens,
        )

        memory_model_name = os.environ.get("MEMORY_MODEL", model.model_name)
        memory_selector = MemorySelector(
            DeepSeekModel(
                client=client, model_name=memory_model_name,
                max_output_tokens=memory_selection_budget.max_output_tokens,
            ),
            context_budget=memory_selection_budget,
        )
        memory_extraction_model = DeepSeekModel(
            client=client, model_name=memory_model_name,
            max_output_tokens=budget.max_output_tokens,
        )

        skill_extraction_model = DeepSeekModel(
            client=client,
            model_name=os.environ.get(
                "SKILL_MODEL",
                model.model_name,
            ),
            max_output_tokens=budget.max_output_tokens,
        )

        skill_extractor = SkillExtractor(
            model=skill_extraction_model,
            context_budget=budget,
        )

        service = BugfixService(
            model=model,
            permission_service=permission_service,
            collapse_model=bugfix_collapse_model,
            memory_store=memory_store,
            memory_selector=memory_selector,
            memory_extraction_model=memory_extraction_model,
            memory_extraction_budget=budget,
            source=source,
            tasks_root=tasks_root,
            budget=budget,
            image=args.image,
            max_steps=args.max_steps,
            skill_store=skill_store,
            skill_selector=skill_selector,
            skill_extractor=skill_extractor,
        )

        result = (
            service.resume(
                args.resume,
                recover_unknown=args.recover_unknown,
            )
            if args.resume
            else service.run(
                args.ticket,
                skill_name=args.skill,
            )
        )

        print("状态：", result["status"])
        print(result.get("answer", result.get("reason")))
        print("模型请求次数：", result["n_calls"])
        skill_outcome = result.get(
            "skill_extraction",
            {"status": "disabled"},
        )

        print("Skill 提取状态：", skill_outcome["status"])
        if result["delivery"]:
            print("修复补丁：", result["delivery"]["patch_path"])
            print("运行报告：", result["delivery"]["report_path"])
        print("运行记录：", result["run_dir"])
        print("任务副本已保留，尚未应用到原工作区。")

        return 0 if result["status"] == "completed" else 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n任务已中断；使用已打印的任务 ID 执行 --resume。缺失结果不会自动重放。")
        raise SystemExit(130)
    except Timeout:
        print("任务正在其他进程中执行，请等待该进程退出后恢复。")
        raise SystemExit(1)
    except (ValueError, RuntimeError, OSError) as exc:
        print("操作失败：", exc)
        raise SystemExit(1)
