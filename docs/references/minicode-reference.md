# MiniCode 实现学习映射

用户更正：主要参考仓库是 LiuMengxuan04/MiniCode，不是 mini-swe-agent。

核对日期：2026-09-26；提交：40413758cc12892528d31c85edbe53e97e25a88d。
已查看目录树并阅读 ARCHITECTURE.md；下表为基于目录和架构说明的阅读路线，不代表已逐文件审查实现。

固定入口：https://github.com/LiuMengxuan04/MiniCode/tree/40413758cc12892528d31c85edbe53e97e25a88d

| 参考文件/目录 | 学习重点 |
| --- | --- |
| src/agent-loop.ts | 模型—工具循环 |
| src/tool.ts、src/tools/ | 工具注册、校验、执行与各工具实现 |
| src/anthropic-adapter.ts、src/mock-model.ts、src/types.ts | 模型适配、假模型与数据契约 |
| src/runtime/、src/abort.ts | 会话运行时、轮次执行、取消 |
| src/session.ts、src/history.ts | 会话事件、父节点、恢复与压缩边界 |
| src/compact/ | 自动/手动压缩、上下文折叠与历史裁剪 |
| src/utils/token-estimator.ts | API 用量边界与本地尾部估算 |
| src/utils/tool-result-storage.ts | 长输出保存、替换和读回 |
| src/memory.ts | 分层项目指令、包含引用、去重和容量控制 |
| src/permissions.ts、src/file-review.ts、src/workspace.ts | 工作区、权限、修改审批 |
| src/config.ts、src/prompt.ts | 配置与提示构建 |

架构说明中的 memory 主要是分层指令文件加载，不直接等于我们要实现的经验筛选、验证与长期知识检索；按需进一步阅读源码后再决定复用范围。会话说明已有 parentUuid 结构，故会话也应结合 MiniCode 与 Pi 一起研究。

保留既定 Python 架构与所有功能。MiniCode 的 TS 运行机制转换为 Python 合适实现；不要求用户学习 TS。现有 lesson01/lesson02 是通用执行循环练习，无需重写。正式目录按核对后的职责逐步确定，不照搬 TS 文件树。
