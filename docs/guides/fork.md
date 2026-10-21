# Fork 子 Agent

主 Agent 通过 `fork_tasks` 派发一个子任务，同步等待精简结果。子任务继承父请求快照，随后使用独立会话、工具集、Skill 和压缩记录运行；不允许递归派发。

## 启动与派发

```powershell
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.app --mode coding --permission-mode default
```

输入：

```text
请调用一次 fork_tasks，派发 review_tests 子任务，agent_type 为 test-reviewer，instruction 为“读取 calculator.py 和 test_calculator.py，分析测试覆盖范围，只读不修改、不运行命令”，skills 为 []，permission_mode 为 plan。子任务返回后汇总结论。
```

每次 `tasks` 列表必须恰好包含一个任务。未指定 `agent_type` 时，使用 `base_tools`，默认 `read_file`、`list_files`。模式未指定则继承；改为其他更宽模式需要程序预先记录用户授权，不能由模型自行批准。所有工具继续经过原有规则与安全检查。

主任务处于 plan 时，只能派发继承 plan 或显式指定 plan 的子任务。

## 子 Agent 定义和 Skill

定义目录优先使用工作区 `.forgepilot/agents/`；不存在时使用项目 `examples/agents/`。例如 `test-reviewer.md`：

```markdown
---
name: test-reviewer
description: 分析测试覆盖范围
tools: read_file, list_files, grep_files, read_artifact
disallowedTools: [edit_file, run_python, run_command, fork_tasks]
skills: []
---
你是测试分析助手。返回相关文件、覆盖范围与尚未验证的事项。
```

`tools` 省略时使用可提供的工具，`disallowedTools` 排除工具。定义中的 `skills` 与派发参数中的 `skills` 合并预加载；未指定预加载时，使用模型选择器。Skill 只能启用范围内已注册的工具。加载结果和定义保存为子任务快照，恢复时复用。

这不是运行中自主调用 Skill 工具的机制，而是启动前模型选择或显式预加载。

## 持久化与恢复

记录位于 `.forgepilot/sessions/forks/<父会话ID>/<作业ID>/`：

```text
job.json
snapshot.json
tasks/<任务ID>/
  session.jsonl
  agent-definition.json    # 使用定义时创建
  skill-state.json         # 配置 Skill 时创建
  artifacts/
  permissions.jsonl
  usage.jsonl
  summary-candidate.json   # 按需创建
  context-state.json       # 按需创建
  memory-recall.json       # 读取记忆时按需创建
```

正常中断后恢复父会话：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.app --resume 父会话ID --mode coding --permission-mode default
```

恢复前重新检查当前权限。已完成的子任务直接回传结果，不重新请求模型；执行中的残留状态在获得执行锁后恢复。如果子工具发出后没有保存结果，恢复停止，不自动重放。检查实际执行现场后，可明确标记未知结果：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.app --resume 父会话ID --mode coding --fork-mark-unknown
```

这不是撤销副作用，也不表示调用失败；只是通知子 Agent 无法确定原结果。父会话同时存在非 Fork 的未知调用时，应先人工检查，不能盲目重新派发。

每次接续最多 8 次子业务模型请求，累计最多 32 次，发送前记录计数，跨进程不清零。摘要与选择器另受各自预算限制。普通超时、连接失败、限流保留为中断；终态失败不自动重新运行。

## 上下文与缓存

子任务独立配置 Snip、Collapse 和前缀摘要。Snip 要求启用 `read_artifact`，否则保留 Collapse 与前缀摘要。压缩不改父会话；继承消息引用的原始工具产物复制到子目录。

实际工具定义、Skill 和压缩后的消息优先于缓存复用。DeepSeek 自动缓存尽力命中共同前缀，没有额外预热请求。`usage.jsonl` 记录子业务模型请求的输入、输出、缓存命中/未命中 token 和耗时；缺失字段为 null，不能当作零。缓存不节省上下文窗口，也不能保证首次请求或并发命中。缓存统计不包含其他辅助模型请求，不能用它直接声称整体成本下降。

## 验收

离线检查：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.fork.check_fork_state tests.fork.check_fork_sessions tests.fork.check_fork_tool tests.fork.check_fork_snapshot tests.fork.check_fork_service tests.fork.check_fork_executor tests.fork.check_fork_factory tests.fork.check_fork_skills tests.fork.check_fork_definitions tests.fork.check_fork_context tests.fork.check_fork_dispatch tests.fork.check_fork_recovery tests.fork.check_fork_usage
```

真实 API 只读案例（会消耗额度，记录保留在 `.forgepilot/fork-api-demos/`）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.diagnostics.smoke_fork_api
```
