from forgepilot.tools.extended_catalog import EXTENDED_TOOL_NAMES


KNOWN_TOOL_NAMES = EXTENDED_TOOL_NAMES | frozenset({
    "read_file",
    "list_files",
    "grep_files",
    "edit_file",
    "write_file",
    "run_python",
    "run_command",
    "read_artifact",
    "read_memory",
    "glob_memory",
    "grep_memory",
    "list_memory_versions",
    "read_memory_version",
    "read_session_notes",
    "fork_tasks",
})
