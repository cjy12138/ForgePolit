# 项目结构与执行链路

`cli/app.py` 负责交互与装配，Harness 组织任务理解和路由，Agent 执行模型与工具循环。

```text
用户输入 → 任务理解/路由 → Skill 与工具配置 → 构建上下文
        → 请求模型 → 校验/授权/执行工具 → 保存结果 → 继续模型循环 → final
```

## 模块职责

| 位置 | 责任 |
|---|---|
| cli | 参数、交互、服务装配与补丁入口 |
| agent.py / harness.py | 模型循环、任务理解与分发 |
| models | API 与内部格式转换、用量和估算 |
| tool_registry.py / tools | 工具定义、校验、执行与重复调用防护 |
| permissions | 规则、边界、模式、审查和确认 |
| sessions / artifacts | 消息历史、请求视图与完整工具输出 |
| checkpoints | Git 快照、预览与代码恢复 |
| memory | Markdown 记忆、召回、提取、笔记和整理 |
| skills / fork | 任务流程与子任务执行状态 |
| environments | 本地命令、Docker 与任务副本 |

原始会话与模型请求视图分离。压缩保留原始记录；Skill、召回和子任务有独立状态。恢复先检查调用与结果是否完整配对，缺失结果返回 needs_recovery。Git 快照不能撤销外部系统副作用。AutoDream 当前整理已有记忆，不直接重读原始会话。

## 本次迁移

| 原位置 | 新位置 |
|---|---|
| exercises/run_deepseek_agent.py | forgepilot/cli/app.py |
| exercises/run_bugfix_agent.py | forgepilot/cli/bugfix.py |
| exercises/apply_bugfix.py | forgepilot/cli/apply_fix.py |
| exercises/export_existing_bugfix.py | forgepilot/cli/export_fix.py |
| exercises/check_*.py | tests/对应功能目录/ |
| inspect 等诊断脚本 | scripts/diagnostics/ |
| 根目录 test.py | examples/tool_smoke.py |
| docs 混合文档 | guides / architecture / references / development |

exercises 保留轻量兼容层。核心模块维持原有导入路径，已有 .forgepilot 和本地工作区不搬动。
