# 测试与流程检查

```bash
python -X utf8 -m tests
python -X utf8 -m tests --suite smoke
```

第一条发现 check_*.py 的 unittest TestCase，第二条运行 10 个 main/assert 流程检查，统计应分别记录。默认不调用真实 API、Docker 或人工审批。不能把导入一个脚本当成执行了它的 main。

## 分组

runtime（执行与路由）、sessions（历史与分支）、context（预算与压缩）、memory（主题与笔记）、skills、fork、permissions、tools、models、checkpoints、environments、bugfix，以及 integration。

```bash
python -m unittest tests.permissions.check_permission_tools
python -m tests.context.check_context_controller
python -m tests.tools.check_extended_tools
```

## 显式集成检查

`integration/check_task_interpreter.py` 调用真实 API；Docker 与多项 bugfix 流程检查需要引擎和镜像；check_command_tool 可能询问用户。查看脚本后再单独运行，不在默认 CI 中执行其 main。

手动诊断位于 scripts/diagnostics。Windows 符号链接、进程等检查可能因平台或权限跳过，跳过不计为通过。CI 已配置；本地成功不等于远程 CI 已经运行。
