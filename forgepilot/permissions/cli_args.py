import argparse

from forgepilot.permissions.models import PermissionRule


class RuleArgumentAction(argparse.Action):
    def __init__(self, *args, rule_action, **kwargs):
        self.rule_action = rule_action
        super().__init__(*args, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        if len(values) not in {1, 2}:
            raise argparse.ArgumentError(self, "需要工具名，以及可选的一个匹配模式")
        try:
            rule = PermissionRule(
                action=self.rule_action,
                tool=values[0],
                pattern=values[1] if len(values) == 2 else None,
            )
        except ValueError as exc:
            raise argparse.ArgumentError(self, str(exc)) from exc
        # 不修改 parser 共享的默认列表。
        rules = list(getattr(namespace, self.dest, None) or [])
        rules.append(rule)
        setattr(namespace, self.dest, rules)


def add_permission_rule_arguments(parser):
    for action in ("allow", "deny", "ask"):
        parser.add_argument(
            f"--{action}",
            dest="permission_rules",
            action=RuleArgumentAction,
            rule_action=action,
            nargs="+",
            metavar="RULE",
            help=f"本次启动的 {action} 规则：工具名 [匹配模式]，可重复指定",
        )
