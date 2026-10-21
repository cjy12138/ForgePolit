"""python -m tests：离线 unittest；--suite smoke：选定的流程检查。"""
import argparse
from pathlib import Path
import subprocess
import sys
import unittest

SMOKE = (
    "tests.runtime.check_agent_resume",
    "tests.sessions.check_session_branch",
    "tests.checkpoints.check_git_checkpoint",
    "tests.context.check_context_controller",
    "tests.context.check_summary_overflow_defense",
    "tests.context.check_session_collapser",
    "tests.memory.check_background_memory",
    "tests.memory.check_memory_recall",
    "tests.skills.check_skill_loader",
    "tests.tools.check_edit_tool",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["unit", "smoke"], default="unit")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.suite == "unit":
        suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="check_*.py", top_level_dir=str(root))
        result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
        return 0 if result.wasSuccessful() else 1
    failures = []
    for module in SMOKE:
        print(f"\nCHECK {module}", flush=True)
        try:
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", module], cwd=root, timeout=60)
            if result.returncode:
                failures.append(module)
        except subprocess.TimeoutExpired:
            failures.append(module + " (timeout)")
    print(f"\n流程检查：{len(SMOKE) - len(failures)}/{len(SMOKE)} 通过")
    for name in failures:
        print("FAILED", name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
