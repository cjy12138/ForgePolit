"""扩展工具的名称与路径字段；不导入运行时，供权限和 Skill 目录共用。"""

EXTENDED_READ_TOOLS = frozenset({
    "glob_files", "directory_tree", "file_info", "file_hash", "read_files",
    "compare_files", "read_json",
    "python_outline", "python_imports", "python_check", "project_info",
    "git_status", "git_diff", "git_log", "git_show_file",
})
EXTENDED_TOOL_NAMES = EXTENDED_READ_TOOLS

EXTENDED_PATH_FIELDS = {
    **{name: ("path",) for name in EXTENDED_TOOL_NAMES},
    "read_files": ("paths",),
    "compare_files": ("left", "right"),
}
