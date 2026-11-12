# 开发上下文自动管理：1.5 天可行性 spike 报告

日期：2026-11-12  
范围：一个真实小任务 + 三个合成检查 fixture + C0/C1/C2 接手对比  
模型：NewHarness `profile1`（Qwen/Qwen3.8-Flash-Next），temperature 0  
定位：课程项目的技术可行性输入，不是产品验收，也不是最终 Harness 的性能结论  
本轮实际模型消耗约 88 万 token（不含一次超时调用），没有做重复运行。

## 结论摘要

1. **“自动记录 → 提取 → 交接 → 新 Agent 接手”这条链路能跑通。** 真实会话产生了 97 个事件；工具失败、测试失败、进程被 kill、重启恢复都能留下可区分记录；交接材料能让新 Agent 完成剩余工作。
2. **但这次没有证明“自动交接材料比没有交接更好”。** C0（只有任务说明 + 仓库）也完成了隐藏测试，而且 token 最少；C2（自动 handoff）比 C0 多约 2.1 倍 token。原因是这个任务的剩余工作从 `TASK.md`、可见测试和 `NotImplementedError` 就能直接看出来，仓库本身就是很强的交接材料。
3. **真正值得继续投入的是事件契约和证据边界，不是“再写一个更长的总结”。** 这次暴露出的关键缺口是：外部编辑没有事件、引用无法证明语义、进程被杀时当前轮对话丢失、严格 tree hash 会把所有旧测试都判 stale。
4. **NewHarness 适合当测试工具。** 它的 Runner、工具注册表、ConversationStore、模型适配器和失败轮记录省掉了搭建 agent 循环的时间；它缺 shell/测试工具、git 快照和上下文层，这三块由我们补。它没有被修改，也不是最终产物。

## 1. 方法

- **开发 run（dev-01）**：一个故意留一半工作的小 Python 仓库。要求先只实现 `parse_pagination`，把 `export_csv` 留给下一个人；中途把错误码从 422 改成 400；测试一次失败、一次通过；通过后重构；在 `sleep` 工具执行时 kill 进程，再重启继续；最后在最后一次测试之后做一次“外部编辑”，制造 stale 证据。
- **确定性检查**：三个 fixture 覆盖状态分类、账本更新（换说法/撤销/重开）、代码变更后的 stale 判定；不调用模型。
- **提取**：对 dev 事件时间线做一次结构化 LLM 抽取，输出带 event id 的目标、约束、决策、完成项、问题、下一步；交接材料由模板确定性渲染，不再调用第二次 LLM。
- **接手对比**：同一任务、同一模型、同一工具，C0 只给任务说明 + 仓库；C1 给完整原始记录；C2 给自动生成的 `handoff.md`。每个条件一个新会话，隐藏测试评分。

## 2. 结果

### 2.1 自动记录与中断

dev-01 共 97 个事件，包括 31 个 `tool_started`、30 个 `tool_finished`、24 条 assistant 消息、4 条用户消息、3 个完成的 turn、1 次 `session_resumed`。

- 初始测试失败被记录为 `run_tests -> failed (exit 1)`；后续通过记录为 `succeeded (exit 0)`。
- kill 发生在 `sleep` 工具执行中：`tool_started` 存在，`tool_finished` 不存在。重启后 `session_resumed`，新会话重新检查仓库并跑测试。**没有把 in-flight 的 sleep 写成成功。**
- 进程被杀时，NewHarness 只持久化到上一个完整 turn；第三轮的对话消息丢失，但文件改动和测试事件仍在 sidecar 里。恢复报告明确写了“哪些保留、哪些缺失”。
- 这次没有真实触发“调用尚未执行就被取消”的 synthetic 路径；它由 fixture 覆盖，不作为真实运行结论。

### 2.2 提取与交接材料

- 第一次抽取调用 60 秒超时；把模型请求超时调到 180 秒、时间线压到 14k 字符后，第二次成功。之后没有再调用模型。
- 抽取正确找出了：总目标、只做 pagination 的阶段约束、标准库约束、422→400 的决策、export_csv 未完成、测试 stale。它也带进了读文件/列目录这类低价值“已完成”项，以及“时间线被截断”这种抽取过程噪声；这些由确定性规则过滤掉，最终交接 9,678 字符、15 条引用。
- 6 条关键结论/边界的人工抽查（agent 自评，不是独立评审）：5 条有事件支持，其中“测试在 tree cde4 通过”已 stale；1 条“外部编辑”只能由当前 worktree 证明，**没有事件引用**。
- 交接材料包含了目标、约束、当前状态、完成项、决策及理由、代码变化、验证（含 stale）、问题与未知、下一步、来源索引。外部编辑这一项是明确的追溯缺口。

### 2.3 更新、持久化与代码关联

- 确定性检查全部通过：状态分类（成功/失败/未执行/中断）；换说法更新、撤销、重新打开；测试后改代码、删除文件、回退；坏引用/跨任务引用。
- 真实 run 里的 422→400 被记录为“400 active、422 superseded”，没有把旧状态当当前状态。
- 外部编辑后，**所有旧测试都被标为 stale**，并列出 `app.py` / `tests/test_pagination.py` 的变化；没有出现“旧测试仍证明当前代码”的错误。
- 严格按 worktree tree hash 判断失效，代价是：只改一行注释也会让全部旧测试 stale。这次没有实现文件级/测试覆盖级失效，误失效率也没有量化；这是下一步要做的策略选择。
- 回退到测试过的 tree 时，旧证据重新可用；删除被测试文件时变 stale。fixture 覆盖了这三种情况。

### 2.4 C0/C1/C2 接手对比

| 条件 | 交给新 Agent 的材料 | 材料字符 | Tokens | Steps | 时间 | 隐藏测试 | 最终答复 |
|---|---|---:|---:|---:|---:|---|---|
| C0 | 任务说明 + 仓库 | 0 | 98,356 | 11 | 97.7s | 通过 | 有 |
| C1 | 完整原始记录 | 32,241 | 334,232 | 11 | 105.6s | 通过 | 有 |
| C2 | 自动 handoff | 9,678 | 206,493 | 12 | 111.9s | 通过 | **无（max_steps）** |

- 三个条件都完成了 `export_csv`，都通过了 8 条隐藏测试，均只使用标准库，没有沿用 422。`parse_pagination` 的 AST 源片段哈希在 dev/C0/C1/C2 完全一致（`2fe289f932bd`），没有重复实现或改坏。
- C2 的 handoff 是原始记录的 30%，token 是 C1 的 61.8%；但仍是 C0 的 2.1 倍。
- C2 在 12 步上限内完成了代码和测试，但没来得及输出最终总结，被 NewHarness 记为 `max_steps`。这是本轮的一个操作性问题：交接材料没有给出“做到什么程度算完成”的停止条件，新 Agent 额外写了很多测试。
- C2 没有调用 `cite` 工具；它读了 handoff 和仓库文件，但没有回查引用。因此本轮“回查负担”为 0，同时也无法说明引用是否真的帮助了接手者。
- **结论：这次任务不能区分 handoff 的价值。** C0 的成功说明“仓库 + 可见测试 + `NotImplementedError`”已经足以接手；自动 handoff 的收益要在一个“剩余工作无法从代码直接看出来”的任务上才会显现。

### 2.5 八个验证点的结论

| 验证点 | 结论 | 依据/缺口 |
|---|---|---|
| 1 自动记录 | 基本通过 | 正常、失败、kill/重启、in-flight 都可区分；未真实覆盖取消/未执行 synthetic 路径 |
| 2 关键提取 | 部分通过 | 目标/约束/决策/剩余项正确；有低价值项和截断噪声，需要过滤；只有 agent 自评 |
| 3 更新持久化 | 通过（fixture）/部分（live） | 换说法、撤销、重开、重启均过；live 只验证了 422→400 |
| 4 代码关联 | 通过 | stale 检测、删除、回退、外部编辑；严格 tree 失效导致全量误伤 |
| 5 自动交接 | 部分通过 | 结构完整、引用可查；外部编辑无事件引用；材料仍偏长 |
| 6 来源追溯 | 部分通过 | 有效/缺失/内容变化/跨任务可查；支持度未独立评审；引用 hash 是后加的 |
| 7 新 Agent 接手 | 存在性通过 | C2 隐藏测试通过，没有原开发者解释；但 C2 没给出最终答复 |
| 8 压缩与价值 | 未证明 | C2 比 C1 省 38% token，但比 C0 贵 2.1 倍；任务太容易，无法体现 handoff 价值 |

## 3. NewHarness 复用评估

**直接复用：** `Runner`、`ToolRegistry`、`ConversationStore`、`OpenAIModel`、`FakeModel`、失败轮补全、token/耗时统计。它让“真实模型 + 工具调用 + 对话落盘”在几小时内可用。

**补的三块：**

1. `run_tests` / `sleep` 工具（NewHarness 默认没有 shell/测试工具）；
2. 包一层 `registry.execute`，在每次调用前后记录 git tree hash、dirty diff、退出码、stdout/stderr；
3. `context.py`：事件归一化、账本、抽取、handoff、引用/失效检查。

**这次踩到的集成问题（已修）：**

- resume 后 Logger 的 `seq` 从 0 开始，破坏全局顺序；已改为从现有最大值继续，并对 dev-01 做了一次 seq 归一化。
- resume 时 manifest 被覆盖，丢掉前两轮；已改为加载旧 manifest 后追加。
- `tool_finished` 没带参数，交接里的命令为空；已补 `arguments`，并在旧事件上通过 `call_id` 回查。
- 测试产生的 `__pycache__` 被当成代码变化；加 `.gitignore`。
- unittest 把结果写到 stderr；capture 必须同时保留 stdout/stderr。
- 第一次抽取时间线 24k 字符被截断，模型据此编出“中间事件缺失”的问题；后来把时间线改成只保留关键事件并压到 14k，但**没有为这次修正再花一次模型调用**，本轮 handoff 是通过确定性过滤修掉的。

## 4. 对最终 Harness 的事件契约建议

1. **事件信封**：`event_id`（稳定、可引用）、`task_id/session_id`、全局单调 `seq`（跨重启不能归零）、`ts`、`actor`、`type`、`status`、`parent/call_id`、payload 引用、内容 hash。
2. **工具事件分开记录 start/finish**：状态至少包含 `proposed / started / succeeded / failed / cancelled / timed_out / not_executed / unknown`；必须带退出码、stdout/stderr、耗时；区分真实结果和内核补的 synthetic 结果。
3. **代码快照**：每次可能改文件的工具调用前后记录 HEAD、tree hash、dirty diff、变更文件；测试证据绑定 `(tree_hash, command, env)`，不是“最近一次测试”。
4. **会话生命周期**：`started / interrupted / resumed / finished`；中断时记录 in-flight call id；恢复报告明确“保留/缺失/未知”。
5. **外部编辑**：要么用 watcher/checkpoint 记录，要么在 handoff 里明确标“未记录变更”，不能假装有引用。
6. **上下文事务**：LLM 只产出候选事项，必须带 event id；程序校验引用存在性，人工/规则决定 active/superseded/withdrawn；历史状态与当前状态分开。
7. **交接快照**：不可变；每条结论带引用；引用带内容 hash；未知单列；生成后代码再变，旧快照不改。
8. **测试分类**：正式测试命令（unittest/pytest）和手工探针分开；只有正式测试的 exit code 能进“验证情况”。

## 5. 限制与下一步

**限制：** 一个任务、一个模型、一次开发 run、三次接手；抽取质量是 agent 自评；C2 没给最终答复；没有独立人类评审；C0 的“无材料”仍包含仓库和测试，不是真正的零上下文；没有真实取消/未执行 synthetic 路径。

**下一步（按价值排序）：**

1. **换一个“剩余工作不能从代码直接看出来”的任务**：例如未写下的产品约束、被否决的方案及理由、失败过的尝试、需要口头确认的验收标准。否则 C0 会一直赢。
2. **加外部编辑 watcher/checkpoint**，让 handoff 能引用“谁在什么时候改了什么”，而不是只说“worktree 变了”。
3. **把 handoff 压到 3–5k 字符**，并加“完成定义/停止条件”，避免 C2 这类 max_steps。
4. **补文件级失效策略**：严格 tree hash 太保守；比较“全量失效”和“按测试覆盖/文件范围失效”的误伤率。
5. **独立评审**：至少让另一个人抽查 5 条结论和 1 次接手，不要只用实现 agent 自评。
6. 保留这次的事件契约、fixtures、`checks.py` 和 `grade.py`；它们可以直接指向定稿 Harness 重跑。

## 6. 产物

- `spike/driver.py`：NewHarness 驱动、git 快照、run_tests/sleep、事件日志、kill/resume。
- `spike/context.py`：事件归一化、账本、抽取、handoff、引用/失效检查。
- `spike/checks.py`：4 组确定性检查（全部通过）。
- `spike/task/`：任务 repo、可见测试、隐藏测试。
- `runs/dev-01/`：97 个事件、handoff、citations、claim_review、recovery。
- `runs/c0/`、`runs/c1/`、`runs/c2/`：三条件接手记录、grade、result。
- `runs/value_summary.json`：对比表。
