import argparse
from pathlib import Path

from forgepilot.bugfix_apply import apply_fix, prepare_apply


def main() -> int:
    parser = argparse.ArgumentParser(
        description="预览并确认应用 bugfix 补丁",
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="预览后询问是否应用",
    )
    args = parser.parse_args()

    try:
        plan = prepare_apply(args.run_dir)

        print("目标工作区：", plan.source)
        print(plan.patch.decode("utf-8", errors="replace"))

        if not args.apply:
            print("检查通过，仅预览，未应用修改。")
            return 0

        try:
            answer = input("输入 yes 应用，其余输入取消：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 0

        if answer != "yes":
            print("已取消，未应用修改。")
            return 0

        backup = apply_fix(plan)
        print("补丁已应用，尚未提交。")
        print("应用前备份检查点：", backup.id)
        print("备份提交：", backup.commit_id)
        return 0

    except (ValueError, RuntimeError, OSError) as exc:
        print("操作失败：", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())