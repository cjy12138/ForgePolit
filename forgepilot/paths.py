"""源码/可编辑安装共用的项目位置，避免入口迁移改变数据目录。"""
from pathlib import Path
from shutil import copytree

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def ensure_demo_workspace() -> Path:
    """保留旧工作区路径；首次运行时从不含本地状态的示例创建。"""
    target = PROJECT_ROOT / "demo_workspace"
    if not target.exists():
        template = PROJECT_ROOT / "examples" / "workspaces" / "calculator"
        copytree(template, target)
    return target
