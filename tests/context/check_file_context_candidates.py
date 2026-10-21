from forgepilot.sessions.files.index import find_file_context_candidates
from forgepilot.sessions.models import MessageEntry


def main():
    branch = []

    def add(payload):
        entry = MessageEntry(
            session_id="demo",
            parent_id=branch[-1].id if branch else None,
            payload=payload,
        )
        branch.append(entry)
        return entry

    root = add({"role": "user", "content": "检查项目。"})

    def interact(call_id, name, path, *, failed=False):
        add({
            "role": "assistant",
            "type": "tool_call",
            "id": call_id,
            "name": name,
            "arguments": {"path": path},
        })
        return add({
            "role": "tool",
            "tool_call_id": call_id,
            "content": "模拟工具结果",
            "is_error": failed,
        })

    interact("read_a", "read_file", "a.py")
    interact("read_b", "read_file", "b.py")
    latest_a = interact("edit_a", "edit_file", "a.py")
    interact("missing", "read_file", "missing.py", failed=True)
    interact("search", "grep_files", "directory")

    before = [entry.model_dump() for entry in branch]

    candidates = find_file_context_candidates(branch)

    assert [item.path for item in candidates] == ["a.py", "b.py"]
    assert candidates[0].tool_name == "edit_file"
    assert candidates[0].result_entry_id == latest_a.id
    print("同一路径取最近成功交互，并按近期程度排序：通过")

    assert all(
        item.path not in {"missing.py", "directory"}
        for item in candidates
    )
    print("失败读取和其他工具不会成为文件候选：通过")

    limited = find_file_context_candidates(branch, max_files=1)
    assert [item.path for item in limited] == ["a.py"]
    assert [entry.model_dump() for entry in branch] == before
    print("候选数量受限，查询不修改历史：通过")

    other = MessageEntry(
        session_id="demo",
        parent_id=root.id,
        payload={"role": "user", "content": "换一个任务。"},
    )
    assert find_file_context_candidates([root, other]) == []
    print("其他分支不会混入这条分支的文件：通过")

    add({
        "role": "assistant",
        "type": "tool_call",
        "id": "unfinished",
        "name": "read_file",
        "arguments": {"path": "pending.py"},
    })

    try:
        find_file_context_candidates(branch)
    except ValueError as exc:
        assert "未完成" in str(exc)
    else:
        raise AssertionError("工具交互未完成时不能进行恢复规划")

    print("缺少结果的调用不能作为成功证据：通过")


if __name__ == "__main__":
    main()