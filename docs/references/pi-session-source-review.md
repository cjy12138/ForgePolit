# Pi 会话与压缩源码核对

核对日期：2026-09-29。固定提交：`cb7969d212836b8939001dce159fbd2ed6ad395f`，仓库入口 https://github.com/badlogic/pi-mono 。本记录来自源文件和部分测试用例的静态阅读，没有执行 Pi 测试，不代表完整审计整个项目。

源码副本保存于 `.forgepilot/references/pi/cb7969d212836b8939001dce159fbd2ed6ad395f/`，该目录已被本项目 Git 忽略规则覆盖。此前 ForgePilot 课程主要参考截图及设计说明；不能追溯性地称为逐项按照 Pi 源码实现。

## 源码依据

以下路径均相对 `packages/coding-agent/`，链接固定到上述提交：

- [session-manager.ts](https://github.com/badlogic/pi-mono/blob/cb7969d212836b8939001dce159fbd2ed6ad395f/packages/coding-agent/src/core/session-manager.ts)：节点类型、父链、上下文投影、追加压缩节点、切换分支。
- [compaction.ts](https://github.com/badlogic/pi-mono/blob/cb7969d212836b8939001dce159fbd2ed6ad395f/packages/coding-agent/src/core/compaction/compaction.ts)：预算、切分、摘要提示、连续压缩和轮次前缀摘要。
- [utils.ts](https://github.com/badlogic/pi-mono/blob/cb7969d212836b8939001dce159fbd2ed6ad395f/packages/coding-agent/src/core/compaction/utils.ts)：历史序列化和文件操作路径提取。
- [agent-session.ts](https://github.com/badlogic/pi-mono/blob/cb7969d212836b8939001dce159fbd2ed6ad395f/packages/coding-agent/src/core/agent-session.ts)：手动/自动压缩调度、取消、树导航运行状态检查。
- [build-context.test.ts](https://github.com/badlogic/pi-mono/blob/cb7969d212836b8939001dce159fbd2ed6ad395f/packages/coding-agent/test/session-manager/build-context.test.ts)：摘要加保留消息、多次压缩、不同分支的上下文。

## 核实的行为与 ForgePilot 取舍

1. `SessionEntryBase`（session-manager.ts:57）用 id、parentId、timestamp 表达树节点。`buildSessionPath`（390）沿父链回溯并反转。ForgePilot 当前消息树方向一致；后续必须将消息节点与其他类型树节点统一，而非让所有节点都依赖 payload。
2. `branch`（1574）验证节点后只修改内存 leafId；`_buildIndex`（1103）加载时沿文件条目更新 leafId。不能把 ForgePilot 的 CursorChanged 持久化事件说成 Pi 的直接移植。保留该自主设计，支持只切换而未追加消息时重启恢复选择。
3. `CompactionEntry`（91）包含 summary 字符串、firstKeptEntryId、tokensBefore，以及可选 details、usage、fromHook、systemMessage。没有我们额外设计的 source_leaf_id 字段。`appendCompaction`（1261）将节点追加到当前叶子之后，并捕获当前系统消息。
4. `buildContextEntries`（476）只选当前父链中的最新压缩节点，组装压缩节点、firstKeptEntryId 起的保留区和压缩后新增条目。随后投影为模型消息；历史节点不删除。ForgePilot 应先落实这条链路，再接自动摘要生成。
5. `branchWithSummary`（1595）用于将离开路径的摘要带到目标分支，类型为 branch_summary，带 fromId。它与预算触发的 compaction 不同。可作为后续可选分支导航能力，不能混作同一种摘要，也不能未经选择自动混入旧分支内容。
6. `shouldCompact`（compaction.ts:289）采用 contextTokens > contextWindow - reserveTokens。源码不是固定 80% 触发；课程此前讨论的比例是自定策略。`estimateContextTokens`（218）结合有效 usage 与尾部估算，estimateTokens 使用 chars/4 启发式；不能把它当作中文 Token 精确计数。ForgePilot 需按供应商语义归一化 usage，包含输出预留、规则、工具 schema 等预算。
7. `findCutPoint`（468）及 prepareCompaction 使用的投影切分路径按 keepRecentTokens 选择边界，不在工具结果处切开。ForgePilot 的 group_entries 可保留为完整工具交互边界机制；keep_recent_groups 只是教学中间方案，后续替换为预算策略。
8. `prepareCompaction`（894）区分旧轮次历史和当前轮次前缀（turnPrefixMessages/isSplitTurn），并提取 previousSummary。`compact`（1016 起）在切开轮次时分别生成历史摘要和轮次前缀摘要，然后拼接；这不是任意数量分块加递归合并的通用实现。ForgePilot 既定长历史分块与合并属于扩展，保留在完整计划中。
9. Pi 默认摘要是带 Goal、Constraints & Preferences、Progress、Key Decisions、Next Steps、Critical Context 的 Markdown 文本（529），不是 Pydantic JSON，也没有逐条强制 source_entry_ids。ForgePilot 的结构化摘要、验证分类与来源引用是自主扩展，可以保留，但引用存在不能证明语义正确。
10. `generateSummaryWithUsage`（718）将历史序列化为待总结文本，加入 previous-summary，通过独立请求生成摘要；拒绝异常结果和工具调用。ForgePilot 可以使用 submit_context_summary 作为结构化返回通道，但它不是可执行工具，不能接文件/命令工具注册表。
11. 手动/自动调度在摘要完成并检查取消后调用 appendCompaction（agent-session.ts:2583、2915）。ForgePilot 同样只在生成、校验和来源边界检查全部成功后发布节点，失败不覆盖原历史。
12. `navigateTree`（3668）拒绝流式运行或压缩期间切换。ForgePilot 目前只有消息配对检查，不是运行互斥；运行状态、锁和坏尾行恢复仍是未完成项。
13. utils.ts 按工具调用提取 read/write/edit 路径，并对摘要输入的单个工具输出做字符截断。这种路径提取本身不证明修改成功。ForgePilot 应结合真实结果与版本证据维护状态，并先保存长输出产物，再提供摘要预览。

## 接下来的课程调整

保留现有代码与测试，不推倒重来；不删除既定功能。

1. 先实现 CompactionEntry 和树节点联合类型，调整存储、分支遍历、状态检查，使压缩节点不被误认成普通消息。
2. 用手工摘要/假摘要验证上下文投影：摘要 + 保留区 + 新消息；加入连续压缩和分支隔离检查。
3. 接入结构化摘要生成、来源校验、取消/失败不发布；保留 JSON 摘要设计为 ForgePilot 扩展。
4. 实现 Token 预算、原始限制独立保留、进行中轮次的前缀处理、previous summary 更新和长历史分块合并。
5. 接入请求前检查、手动/自动压缩、超限有限重试，记录 usage，保证不因请求重试而重放已执行工具。

压缩摘要不自动成为长期记忆。Git 文件恢复、长期记忆、插件生命周期和项目授权等原有计划仍保留。
