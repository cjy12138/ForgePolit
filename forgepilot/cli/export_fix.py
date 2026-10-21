import argparse
import json
from pathlib import Path

from forgepilot.bugfix_delivery import export_bugfix_result
from forgepilot.environments.bugfix_workspace import BugfixWorkspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    metadata = json.loads(
        (run_dir / "task.json").read_text(encoding="utf-8")
    )
    result = json.loads(
        (run_dir / "result.json").read_text(encoding="utf-8")
    )

    task = BugfixWorkspace(
        id=metadata["task_id"],
        path=Path(metadata["task_workspace"]),
        source_workspace=Path(metadata["source_workspace"]),
        baseline_commit_id=metadata["baseline_commit_id"],
    )

    delivery = export_bugfix_result(
        task=task,
        result=result,
        output_dir=run_dir,
    )

    for name, path in delivery.items():
        print(name, "：", path)


if __name__ == "__main__":
    main()