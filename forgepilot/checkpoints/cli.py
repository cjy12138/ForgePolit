from pathlib import Path

from forgepilot.checkpoints.git import GitCheckpointStore
from forgepilot.checkpoints.service import SessionCheckpointService
from forgepilot.sessions.store import SessionStore

def run_checkpoint_action(
    action: str,
    *,
    sessions: SessionStore,
    workspace: Path,
) -> None:
    service = SessionCheckpointService(
        sessions,
        GitCheckpointStore(workspace),
    )

    if action == "save":
        record = service.create()

        print("已保存检查点：", record.id)
        print("关联会话节点：", record.target_entry_id)
        print("Git 提交：", record.commit_id)
        return

    if action not in {"preview", "restore"}:
        raise ValueError("不支持的检查点操作")

    plan = service.prepare_current_restore()

    print("目标检查点：", plan.target.id)
    print("恢复前备份：", plan.backup.id)
    print("文件变化：")
    print(plan.changes or "没有文件差异。")
    print("内容差异：")
    print(plan.patch or "没有内容差异。")

    if action == "preview":
        return

    try:
        answer = input(
            "输入 RESTORE 执行恢复，其他输入取消："
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消恢复。")
        return

    if answer != "RESTORE":
        print("已取消恢复，工作区文件未改变。")
        return

    restored = service.restore_current(plan)

    print("已恢复代码检查点：", restored.id)
    print("恢复通知已写入会话，尚未调用模型。")
    print("再次使用 --resume 可继续执行。")

def run_checkout_action(
    target_leaf_id: str,
    *,
    sessions: SessionStore,
    workspace: Path,
) -> None:
    service = SessionCheckpointService(
        sessions,
        GitCheckpointStore(workspace),
    )

    plan = service.prepare_checkout(target_leaf_id)

    print("当前对话节点：", plan.source_leaf_id)
    print("目标对话节点：", plan.target_leaf_id)
    print("目标检查点：", plan.checkpoint.id)
    print(
        "检查点关联节点：",
        plan.checkpoint.target_entry_id,
    )
    print("恢复前备份：", plan.git_plan.backup.id)

    if (
        plan.checkpoint.target_entry_id
        != plan.target_leaf_id
    ):
        print(
            "目标节点继承祖先的检查点，"
            "恢复的是该检查点保存的代码。"
        )

    print("文件变化：")
    print(plan.git_plan.changes or "没有文件差异。")

    print("内容差异：")
    print(plan.git_plan.patch or "没有内容差异。")

    try:
        answer = input(
            "输入 CHECKOUT 恢复代码并切换对话，"
            "其他输入取消："
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消切换恢复。")
        return

    if answer != "CHECKOUT":
        print("已取消，对话位置和工作区代码未改变。")
        return

    restored = service.checkout(plan)

    print("已恢复代码检查点：", restored.id)
    print("已切换对话，恢复通知已写入新分支。")
    print("尚未调用模型；再次使用 --resume 可继续。")