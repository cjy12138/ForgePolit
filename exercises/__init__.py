"""旧教学命令兼容层；新代码位于 forgepilot.cli、tests 和 scripts。"""
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
# 保留 python -m exercises.check_*，而不复制 167 个历史脚本。
__path__.extend(str(path) for path in sorted((_root / "tests").glob("*"))
                if path.is_dir() and path.name != "__pycache__")
__path__.append(str(_root / "scripts" / "diagnostics"))
