import json
import subprocess
from pathlib import Path

from forgepilot.checkpoints.git import GitCheckpointStore
from forgepilot.environments.bugfix_workspace import BugfixWorkspace


def export_bugfix_result(
    *,
    task: BugfixWorkspace,
    result: dict,
    output_dir: Path,
) -> dict:
    output_dir = output_dir.resolve()

    if (
        output_dir.is_relative_to(task.path.resolve())
        or output_dir.is_relative_to(task.source_workspace.resolve())
    ):
        raise ValueError("交付文件必须放在任务副本和原工作区之外")

    final_checkpoint = GitCheckpointStore(task.path).create()

    diff = subprocess.run(
        [
            "git",
            "-C",
            str(task.path),
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            task.baseline_commit_id,
            final_checkpoint.commit_id,
            "--",
        ],
        capture_output=True,
        timeout=30,
        shell=False,
    )

    if diff.returncode != 0:
        raise RuntimeError(
            diff.stderr.decode("utf-8", errors="replace")
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    patch_path = output_dir / "repair.patch"
    patch_path.write_bytes(diff.stdout)

    messages = result.get("messages", [])
    calls = {}

    for message in messages:
        if message.get("role") != "assistant":
            continue

        if message.get("type") == "tool_call":
            calls[message["id"]] = message

        elif message.get("type") == "tool_batch":
            for call in message["calls"]:
                calls[call["id"]] = call

    lines = [
        "# Bugfix 运行报告",
        "",
        f"- 任务 ID：{task.id}",
        f"- Agent 状态：{result['status']}",
        f"- 模型请求次数：{result.get('n_calls', 0)}",
        f"- 起点提交：{task.baseline_commit_id}",
        f"- 结束快照：{final_checkpoint.commit_id}",
        f"- 补丁是否为空：{'是' if not diff.stdout else '否'}",
        "",
        "Agent 完成不等于独立验收通过；以下保留实际命令结果。",
        "",
        "## 命令执行记录",
    ]

    for message in messages:
        if message.get("role") != "tool":
            continue

        call = calls.get(message.get("tool_call_id"))

        if call is None or call["name"] not in {
            "run_command",
            "run_python",
        }:
            continue

        lines.extend([
            "",
            f"### {call['name']} / {call['id']}",
            "",
            "参数：",
            "```json",
            json.dumps(call["arguments"], ensure_ascii=False, indent=2),
            "```",
            "",
            f"退出码：{message.get('exit_code')}",
            f"工具错误：{message.get('is_error')}",
            "",
            "实际输出：",
            "```text",
            message.get("content", ""),
            "```",
        ])

    report_path = output_dir / "report.md"
    report_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    manifest_path = output_dir / "delivery.json"
    manifest_path.write_text(
        json.dumps(
            {
                "task_id": task.id,
                "baseline_commit_id": task.baseline_commit_id,
                "final_commit_id": final_checkpoint.commit_id,
                "patch_empty": not bool(diff.stdout),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "patch_path": patch_path,
        "report_path": report_path,
        "manifest_path": manifest_path,
    }