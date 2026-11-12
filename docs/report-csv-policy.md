# CSV policy task：一个能区分 C0/C1/C2 的任务

日期：2026-11-12  
定位：补充任务，用来验证“上下文确实不在仓库里”时自动 handoff 是否产生差异  
模型：NewHarness `profile1`（Qwen/Qwen3.8-Flash-Next），temperature 0  
新增任务实际消耗约 36 万 token（不含两次抽取超时）；handoff 本身没有调用模型。

## 1. 为什么上一个任务区分不出来

上一个 pagination/CSV 任务的剩余工作可以从 `TASK.md`、可见测试和
`NotImplementedError` 直接推出来。C0 只拿仓库也能完成，所以 handoff 的
价值被仓库本身掩盖了。

新任务把“必须知道、但仓库里没有”的内容做成产品策略：

- 行终止符必须是 LF，不能是 CRLF；下游按 `\n` 切分。
- 布尔值必须是小写 `true` / `false`，不是 Python 的 `True` / `False`。
- `None` 和缺失字段必须是空字段。
- 后续行中不在表头里的键必须忽略。
- 所有值都为空的行必须跳过，不能产生空行。
- 空输入返回空字符串。

这些策略只写在开发会话的 `dev_brief.md` / 用户消息里，不在仓库、
可见测试或 takeover brief 里。C0 只能猜；C1 能从原始记录里找；C2 从
handoff 里读。

## 2. 任务设计

```
spike/tasks/csv-policy/
  dev_brief.md          # full product decisions; dev session and task_brief event
  brief.md              # takeover brief; deliberately omits the decisions
  repo/
    app.py              # initial stub; dev session leaves a happy-path version
    tests/test_export.py # visible tests: header/rows/quoting only
    TASK.md             # generic pointer, no policy details
  hidden/test_hidden.py # 8 acceptance tests for the policy
  prompts/dev.json      # one-turn happy-path implementation
  make_handoff.py       # deterministic handoff from explicit decision bullets
  summarize.py          # C0/C1/C2 comparison
```

开发会话只实现 happy path：表头、`str()`、LF、csv 模块引用；布尔/None/
缺失/多余/空行策略被明确留给接手者。初始代码用 `csv.writer` 和
`str(row[key])`，所以 C0 会看到一个“看起来快完成”的实现，而不是一个空
stub。可见测试只检查基本 CSV，不暴露策略；8 条隐藏测试才检查策略。

`csv-policy` 的 handoff 由 `make_handoff.py` 从记录的决策 bullet 确定性
生成，没有调用 LLM——LLM 抽取在这个小任务上连续两次超时。它的目的不是
比较抽取质量，而是给 C2 一个“完整且正确的上下文”上界。

## 3. 结果

| 条件 | 材料 | 材料字符 | Tokens | Steps | 时间 | 隐藏测试 | 最终答复 |
|---|---|---:|---:|---:|---:|---|---|
| C0 | task brief + repo | 0 | 83,140 | 12 | 141.4s | **失败（6/8）** | 无（max_steps） |
| C1 | 完整原始记录 | 6,827 | 101,535 | 12 | 107.4s | 通过 | 无（max_steps） |
| C2 | 自动 handoff | 5,047 | 135,635 | 12 | 95.4s | 通过 | 有 |

C0 猜对了 LF、布尔小写、None、缺失/多余键，但没有实现“跳过全空行”，
因此 `test_all_empty_rows_are_skipped` 和 `test_header_only_when_all_rows_empty`
失败。C1 和 C2 都通过 8/8。

- **C0 失败，C1/C2 成功**：这是上一个任务没有出现的差异，说明“不在仓库里的
  上下文”确实会影响接手结果。
- **C2 最快（95.4s），C1 次之（107.4s），C0 最慢（141.4s）**：C2 的 handoff
  直接把策略放在面前，少了探索；C0 花了更多时间试错。
- **C2 不是最省 token**：C2 135,635 > C1 101,535 > C0 83,140。handoff 比原始
  记录短（5,047 vs 6,827 字符，约 74%），但 C2 的 token 反而更高；可能原因
  是它读了 handoff 和仓库，并写了更多验证。**压缩材料不等于压缩总成本。**
- **只有 C2 输出了最终答复**；C1/C0 都在 12 步上限内完成任务/失败，没有
  最终总结。C1 的隐藏测试通过，但 `final_answer=false`。
- 三个条件都没有引入第三方依赖；`export_csv` 是唯一公共函数，stdlib 检查通过。
- C2 调用了 2 次 `cite`，说明 handoff 的引用通道被实际用上；C0 没有任何来源
  可查，C1 直接在原始记录里搜索。C2 的 7 次读文件 + 2 次引用与它比 C1 多花
  token 的结果一致，但本轮没有逐项归因。

## 4. 这说明什么

1. **任务设计比 handoff 本身更影响结论。** 上一个任务 C0 赢；这个任务 C0
   失败。差别不在模型，而在“剩余工作需要的信息是否在仓库里”。
2. **自动 handoff 的收益是“完整性和速度”，不一定是 token。** C2 最快、唯一
   有最终答复，但 token 最高。如果项目目标是降低总 token/成本，需要继续
   压缩 handoff 或减少 agent 的重复验证；如果目标是减少接手失败和澄清，
   C2 的收益更明显。
3. **原始记录也能完成任务。** C1 通过且比 C2 省 34k token，只是慢 12 秒；
   对 5k 字符的小任务，完整记录本身并不过载。handoff 的价值要在更长、
   更嘈杂的记录上才会更明显。
4. **C0 的失败不是“完全不知道”，而是漏掉一条策略。** 它猜对了大部分规则，
   只漏了“跳过全空行”。这说明非显式上下文的风险常常是“差一点”，而不是
   “全错”；隐藏测试正好能抓住这种差异。

## 5. 限制

- 一个任务、一个模型、每个条件一次；C0/C1 还撞了 12 步上限。
- `csv-policy` 的 handoff 是确定性规则生成，不是 LLM 抽取；它代表“上下文
  完整且正确”的上界，不代表自动抽取的平均质量。
- 隐藏测试的 8 条策略是人为设计的；C0 的失败幅度依赖这些策略是否可猜。
- C2 的 token 更高，原因没有通过逐项归因（读 handoff、读仓库、写测试）拆开。
- 没有独立人类评审。

## 6. 下一步

1. 把任务再放大一点：多文件、多个未写下的约束、一个被否决的方案，观察
   C1 的原始记录是否开始过载、C2 是否仍能保持完整。
2. 给 C2 的 handoff 加“完成定义/停止条件”，避免 max_steps。
3. 对 C2 的 token 做归因：handoff 读取、仓库探索、测试编写分别多少。
4. 至少让另一个人抽查 handoff 和接手结果；当前是 agent 自评。
5. 保留 `tasks/` 的目录约定：每个任务自带 repo、brief、hidden、prompts；
   运行结果按 `runs/<task>/<condition>/` 分组，不把新任务文件散在根目录。

## 7. 产物

- [handoff](../runs/csv-policy/dev-01/handoff.md)
- [dev events](../runs/csv-policy/dev-01/events.jsonl)
- [C0/C1/C2 对比](../runs/csv-policy/value_summary.json)
- [任务说明](../spike/tasks/csv-policy/brief.md) / [开发 brief](../spike/tasks/csv-policy/dev_brief.md)
- [隐藏测试](../spike/tasks/csv-policy/hidden/test_hidden.py)
