import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from forgepilot.checkpoints.git import GitCheckpointStore


@dataclass(frozen=True)
class BugfixApplyPlan:
    source: Path
    baseline_commit_id: str
    patch: bytes


def git(
    source: Path,
    args: list[str],
    *,
    input_data: bytes | None = None,
) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(source), *args],
        input=input_data,
        capture_output=True,
        timeout=30,
        shell=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace")
        )

    return result.stdout


def tree_id(source: Path, commit_id: str) -> bytes:
    return git(
        source,
        ["rev-parse", f"{commit_id}^{{tree}}"],
    ).strip()


def check_source(plan: BugfixApplyPlan):
    # 保存当前状态，既用于比较，也作为应用前备份。
    checkpoint = GitCheckpointStore(plan.source).create()

    if tree_id(plan.source, checkpoint.commit_id) != tree_id(
        plan.source,
        plan.baseline_commit_id,
    ):
        raise ValueError(
            "原工作区代码已变化，请重新运行修复或人工合并"
        )

    return checkpoint


def prepare_apply(run_dir: Path) -> BugfixApplyPlan:
    run_dir = run_dir.resolve()

    task = json.loads(
        (run_dir / "task.json").read_text(encoding="utf-8")
    )
    delivery = json.loads(
        (run_dir / "delivery.json").read_text(encoding="utf-8")
    )

    if (
        task["task_id"] != delivery["task_id"]
        or task["baseline_commit_id"] != delivery["baseline_commit_id"]
    ):
        raise ValueError("交付记录与任务信息不一致")

    baseline = delivery["baseline_commit_id"]
    final = delivery["final_commit_id"]

    for commit_id in (baseline, final):
        if not isinstance(commit_id, str) or not re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
            commit_id,
        ):
            raise ValueError("提交 ID 格式不合法")

    source = Path(task["source_workspace"]).resolve()
    patch = (run_dir / "repair.patch").read_bytes()

    expected_patch = git(source, [
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-textconv",
        baseline,
        final,
        "--",
    ])

    if patch != expected_patch:
        raise ValueError("补丁与交付快照不一致，请重新导出")

    if not patch:
        raise ValueError("补丁为空，没有需要应用的修改")

    plan = BugfixApplyPlan(
        source=source,
        baseline_commit_id=baseline,
        patch=patch,
    )

    check_source(plan)
    git(source, ["apply", "--check", "-"], input_data=patch)

    return plan


def apply_fix(plan: BugfixApplyPlan):
    # 用户确认期间，代码可能发生变化，所以再检查一次。
    backup = check_source(plan)

    git(
        plan.source,
        ["apply", "--check", "-"],
        input_data=plan.patch,
    )
    git(
        plan.source,
        ["apply", "-"],
        input_data=plan.patch,
    )

    return backup