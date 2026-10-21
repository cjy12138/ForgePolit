import argparse
from pathlib import Path

from filelock import Timeout

from forgepilot.permissions.models import PermissionRule
from forgepilot.permissions.rule_store import PermissionRuleStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="查看、添加和删除权限规则")
    parser.add_argument(
        "--workspace", type=Path,
        default=Path(__file__).resolve().parents[2] / "demo_workspace",
        help="原项目工作区；默认 demo_workspace",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="按来源列出规则及文件路径")
    listing.add_argument("--source", choices=["all", "policy", "user", "project", "local"], default="all")
    adding = commands.add_parser("add", help="添加持久规则，重复规则不会重复写入")
    adding.add_argument("--source", choices=["user", "project", "local"], default="local")
    adding.add_argument("--action", choices=["allow", "deny", "ask"], required=True)
    adding.add_argument("--tool", required=True)
    adding.add_argument("--pattern", help="省略时匹配该工具的所有调用")
    removing = commands.add_parser("remove", help="按来源和 list 显示的序号删除规则")
    removing.add_argument("--source", choices=["user", "project", "local"], default="local")
    removing.add_argument("index", type=int, help="该来源中的规则序号，从 1 开始")
    args = parser.parse_args(argv)

    try:
        store = PermissionRuleStore(workspace=args.workspace)
        if args.command == "list":
            sources = list(store.paths) if args.source == "all" else [args.source]
            for source in sources:
                print(f"[{source}] {store.path_for(source)}")
                rules = store.list_rules(source)
                if not rules:
                    print("  无规则")
                for index, rule in enumerate(rules, 1):
                    pattern = "全部调用" if rule.pattern is None else repr(rule.pattern)
                    print(f"  {index}. {rule.action} {rule.tool} {pattern}")
            print("任何来源的 deny 优先；启动临时规则不会保存到这些文件。")
        elif args.command == "add":
            index, added = store.add(args.source, PermissionRule(
                action=args.action, tool=args.tool, pattern=args.pattern,
            ))
            print(f"{'已添加' if added else '规则已存在'}：[{args.source}] 第 {index} 条")
            print("配置路径：", store.path_for(args.source))
            print("下次启动生效，正在运行的会话不会自动重载。")
        else:
            rule = store.remove(args.source, args.index)
            print(f"已删除：[{args.source}] {rule.action} {rule.tool} {rule.pattern!r}")
            print("下次启动生效，正在运行的会话不会自动重载。")
    except (ValueError, OSError, Timeout) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
