# Bugfix 使用与验收

Bug Ticket → 独立 Git worktree → Docker 中执行命令与测试 → 补丁和报告 → 人工预览并应用。

Python 自实现核心；借鉴 mini-swe-agent 的独立命令执行方式。每次命令使用新的 `bash -lc` 进程，文件修改保留，不依赖前次命令的 cwd 或临时环境变量。没有基准测试支持优于其他 coding Agent 的结论。

## 环境与启动

原工作区必须是有 HEAD 提交的 Git 仓库根目录。Windows 使用 Linux Docker 引擎，预先拉取 `python:3.13-slim`。模型 API 在宿主机调用，任务容器使用 `--network none`；依赖需预装在镜像或已有文件中。

项目根目录 `.env` 配置 `DEEPSEEK_API_KEY`、可选 `DEEPSEEK_MODEL` 和 `MEMORY_MODEL`；不要提交密钥。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.bugfix --workspace "D:\program\ForgePilotBugfixDemo" --ticket "修复 calculator.py 的 add。只修改 calculator.py，不修改测试文件。先运行 unittest，修复后重新运行相同测试。"
```

也可从统一入口选择 bugfix：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.app --workspace "D:\program\ForgePilotBugfixDemo" --mode bugfix
```

原工作区的未提交修改会纳入起始快照。任务目录默认在 `%USERPROFILE%\.forgepilot\bugfix-workspaces`，通过 `--tasks-root` 指定其他位置时，必须放在原仓库之外。

## 产物

每次运行打印任务 ID 和记录目录。`_runs/<TASK_ID>/` 包含：

- `task.json`：原工作区、任务副本、原始 Ticket、镜像和基线提交。
- `session.jsonl`：独立任务会话及压缩记录。
- `artifacts/`：工具完整输出，可通过 `read_artifact` 读回。
- `result.json`：本次 Agent 状态和请求视图；原始证据以会话和产物为准。
- `repair.patch`、`delivery.json`、`report.md`：补丁、快照标识与完整命令证据。
- `memory-recall.json`：该任务独立召回缓存和读取额度。
- `memory-result.json`、`session.memory-extraction.json`：提取结果及已处理轮次。

`completed` 表示模型完成回答，不自动等于修复验收通过。报告保留实际命令和退出码；人工仍应检查补丁和测试范围。模型提示要求遵守修改范围，尚未实现按 Ticket 自动解析出的强制文件写入授权。

## 中断恢复

```powershell
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.bugfix --resume TASK_ID
```

将 `TASK_ID` 替换为打印的 32 位小写十六进制任务 ID。恢复时不再传 `--workspace` 和 `--ticket`；自定义任务根目录需再次传入同一 `--tasks-root`。

恢复读取原副本、基线、会话、产物和召回状态。会关闭该任务遗留的命名容器并重新启动；文件保留，但原容器里临时安装的依赖和后台进程不会保留。使用任务锁拒绝同一任务并行恢复。已完成会话不会再次请求业务模型，未处理的记忆提取仍可执行。

若工具调用已保存、结果却缺失，恢复先返回 `needs_recovery`。先检查任务副本当前状态，然后显式执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.bugfix --resume TASK_ID --recover-unknown
```

该选项只补记 `execution_unknown`，不重放旧调用，不把它当作成功或失败的实际输出。继续执行的 Agent 应先检查现场再决定下一步。正常 Ctrl+C 会清理容器；强制杀死进程后，下次恢复清理该任务容器。

## 预览与应用

```powershell
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.apply_fix "C:\Users\Administrator\.forgepilot\bugfix-workspaces\_runs\TASK_ID"
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.apply_fix "C:\Users\Administrator\.forgepilot\bugfix-workspaces\_runs\TASK_ID" --apply
```

第一条只预览。第二条展示差异并要求输入 `yes`；应用前保存备份检查点，不自动 Git commit 或 push。原工作区已偏离任务基线时拒绝应用，需要人工合并或重新修复。

## 上下文与记忆

Snip 在输入预算约 70% 尝试缩短旧工具输出；Collapse 在约 80% 后台准备合法局部片段摘要；前缀压缩在约 90% 总结较早历史。每层处理后重新估算，不保证每次执行所有层。Collapse 候选由主线程在请求前校验并发布；达到前缀阈值不等待后台。原始历史和输出保留。

同一用户、项目、原工作区共享 Markdown 记忆目录；coding 和每个 bugfix 任务有各自的会话、召回缓存与累计额度。两个入口共用 `project_memory_root()`，默认使用 `local-user/forgepilot-demo` 范围。记忆不能授予权限或覆盖当前任务限制，当前代码事实需重新验证。

完成任务后从 bugfix 原始会话提取可复用记忆，更新主题 Markdown 和 MEMORY.md 索引，并登记来源与已处理轮次。复用已有版本备份和发布检查；同一轮不重复提取。bugfix 当前同步等待提取，提取失败不改变修复状态，交付物先保存。日常 AutoDream 整理由已有 coding 入口调度，独立 bugfix 入口不额外创建第二套整理器。

## 离线验收与实际边界

```powershell
.\.venv\Scripts\python.exe -X utf8 -m tests.integration.check_bugfix_recovery
.\.venv\Scripts\python.exe -X utf8 -m tests.integration.check_bugfix_memory
.\.venv\Scripts\python.exe -X utf8 -m tests.integration.check_bugfix_snip
.\.venv\Scripts\python.exe -X utf8 -m tests.integration.check_bugfix_compaction
.\.venv\Scripts\python.exe -X utf8 -m tests.integration.check_bugfix_collapse
.\.venv\Scripts\python.exe -X utf8 -m tests.integration.check_bugfix_background_collapse
```

使用临时 Git 仓库、Docker 和脚本模型，不请求真实 API。恢复验收实际运行失败测试、修复和复测，在测试创建的临时原仓库中预览并应用补丁。压缩调度检查中的 token 估算是模拟值，不证明真实压缩率或模型修复能力。

容器仅挂载任务副本，不挂载原工作区或 Docker socket；文件工具在宿主机处理任务副本。worktree 仍与原仓库共享 Git 元数据，Docker 默认用户和资源限制未加固，不能把本实现当作运行任意不可信代码的完整安全沙箱。员工认证、仓库授权和插件体系属于后续项目主线。
