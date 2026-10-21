# 会话与上下文模块

根目录保留会话树的基础模块：`models.py`、`store.py`、`validation.py`、`grouping.py`、`context.py`、`inspection.py`、`integrity.py`。子包按功能组织，`__init__.py` 保持轻量，不集中导入所有模块。

| 子目录 | 职责 | 主要入口 |
| --- | --- | --- |
| `budget/` | 单次上下文预算、Token 粗估、摘要作业预算 | `context_budget.py`、`token_estimator.py`、`summary_job.py` |
| `cache/` | 候选结果缓存、磁盘快照、配置标识 | `candidates.py`、`snapshot.py`、`profile.py` |
| `snip/` | 工具输出截断、记录、校验、候选和请求视图 | `transform.py`、`records.py`、`planner.py`、`request_view.py` |
| `summarization/` | 摘要 Schema、生成、分块合并、来源元数据 | `schema.py`、`summarizer.py`、`chunked.py`、`artifacts.py` |
| `compaction/` | 前缀压缩计划、预算边界、发布与收益检查 | `planning.py`、`token_planning.py`、`compactor.py`、`benefit.py` |
| `collapse/` | 局部折叠；目前已实现范围规划 | `planning.py` |
| `files/` | 当前文件候选、读取与预算内恢复 | `index.py`、`restore.py`、`budget.py` |
| `control/` | 上下文调度、统计、熔断状态与停止分类 | `controller.py`、`stats.py`、`circuit_state.py`、`outcomes.py` |

例如，原来 `from forgepilot.sessions.budget import ContextBudget`，现在使用 `from forgepilot.sessions.budget.context_budget import ContextBudget`。工程、练习的导入、动态导入及 mock 路径已同步迁移；后续课程使用新路径。

目录调整不改变会话 JSONL、artifact 或候选快照的存储位置与格式，也不新增 Collapse 的运行功能。
