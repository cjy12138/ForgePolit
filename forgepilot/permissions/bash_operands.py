from forgepilot.permissions.bash_analysis import BashOperation


# 只解释语义明确的常见文件命令。复杂选项留给后续审查。
PATH_COMMANDS = {
    "cat": "nbsETuvA",
    "head": "qv",
    "tail": "qvfF",
    "ls": "alhRrtS1d",
    "wc": "clwmL",
    "stat": "L",
    "rm": "rfivd",
    "cp": "rRafipv",
    "mv": "fivn",
    "touch": "acm",
    "mkdir": "pv",
    "rmdir": "pv",
    "tee": "ai",
    "cd": "LP",
}
VALUE_OPTIONS = {
    "head": {"-n", "-c", "--lines", "--bytes"},
    "tail": {"-n", "-c", "--lines", "--bytes"},
}


def extract_path_operands(operation: BashOperation) -> tuple[list[str], bool]:
    """返回确定的文件操作数，以及是否还有未解释的内容。"""
    name = operation.static_name
    # 不信任 /some/path/cat 与系统 cat 语义一致。
    if name not in PATH_COMMANDS:
        return [], True

    paths = []
    unresolved = False
    options_finished = False
    skip_value = False
    for argument in operation.static_arguments:
        if skip_value:
            skip_value = False
            # 选项值不被当成文件路径。
            if argument is None:
                unresolved = True
            continue
        if argument is None:
            unresolved = True
            continue
        if not options_finished and argument == "--":
            options_finished = True
            continue
        if not options_finished and argument.startswith("-") and argument != "-":
            if argument in VALUE_OPTIONS.get(name, set()):
                skip_value = True
            elif len(argument) > 1 and not argument.startswith("--") and all(
                char in PATH_COMMANDS[name] for char in argument[1:]
            ):
                pass
            else:
                # 未知选项可能消费下一个参数，不能猜测其语义。
                unresolved = True
                break
            continue
        if argument != "-":
            paths.append(argument)
    return paths, unresolved or skip_value
