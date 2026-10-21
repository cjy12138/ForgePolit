# 开发说明

使用 Python 3.12 或更高版本，建议与 CI 一致使用 3.13。

```bash
python -m venv .venv
# 激活本地虚拟环境后
python -m pip install -e .
python -m tests
python -m tests --suite smoke
```

核心模块放在 `forgepilot/`；CLI 装配放在 `forgepilot/cli/`；测试按功能放入 `tests/` 对应目录。新增工具需要声明 Pydantic 输入、用途和权限行为，并更新工具目录与文档。

`check_*.py` 包含 unittest 与早期 main/assert 流程检查两种形式。默认测试命令发现 unittest，smoke 命令单独运行选定流程；不要把“导入了某模块”当作运行了其 main 检查。需要 API、Docker 或人工确认的脚本位于 `tests/integration/` 和 `scripts/diagnostics/`，不在默认离线流程中执行。

不要提交 `.env`、真实会话、工具产物、工作区私有代码、生成简历或运行日志。禁止在默认测试/CI 中调用付费模型 API；集成检查必须显式运行。

历史命令 `python -m exercises.check_*` 仍可在源码目录使用，新增文档优先采用正式模块路径。项目目前以源码及可编辑安装为主，尚未承诺独立 wheel 的资源/用户数据目录兼容性。
