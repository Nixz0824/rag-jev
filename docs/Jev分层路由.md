# Jev 分层路由评测

生成时间：2026-09-29 13:59　案例：8 条　检索口径：BM25（不依赖本地向量服务，便于复现）

> **口径**：live：调用真实 TypeSafe API，槽位判断按案例期望评分。
> 命中判据是「引擎实际返回的证据列表里第 1 / 前 5 条是否为该案例的期望行」，期望行由 `expect.rows` 从语料里解析，数值取自官方公告。
> 本报告同时给出负面结果：路由没有改变结果的题数、以及路由改错的题数。

## 三口径对照

| 口径 | 命中@1 | 命中@5 | 槽位正确 | 路由调用次数 | 路由成本 | 路由耗时 | 分叉题数 | 平均总耗时 |
|---|---|---|---|---|---|---|---|---|
| off | 7/8 | 8/8 | —（未提问） | 0 | $0.000000 | 0ms | 0/8 | 0ms |
| shadow | 7/8 | 8/8 | 7/8 | 18 | $0.000285 | 11748ms | 2.3/8 | 498ms |
| active | 7/8 | 8/8 | 7/8 | 18 | $0.000285 | 12370ms | 2/8 | 524ms |

「槽位正确」只统计**路由器真的提问过**的案例：`routing=off` 不提问，低置信槽位既不算答对也不算答错，因此该列显示「未提问」，而不是把「没问」记成「答错」。

**重复 3 次**（模型有随机性，单次结果不足以支撑结论）。每轮命中@1：

| 口径 | 每轮命中@1 | 均值 |
|---|---|---|
| off | 7、7、7 | 7.0/8 |
| shadow | 7、7、7 | 7.0/8 |
| active | 7、7、7 | 7.0/8 |

本次没有跨轮次翻转的案例：所有案例在每一轮的命中情况都相同。

- `shadow` 与 `active` 的调用统计相同（各 18 条案例记录到调用）：shadow 照常做语义判断，只是不把结果用于检索。因此两者的差异只能来自「是否使用路由」，而不是「是否运行路由」。
- 本次运行实际发起的语义调用：**18 次**（下表按案例累计为 18 次）。两者相等：本次每个案例都真实发起了调用。
- 每问平均语义调用 **0.86** 次（只按需要判断的问题计，分母 7.0 条）。
- 检索口径为 BM25，与生产默认的 hybrid 会有细微差异；这里比较的是路由带来的增量，不是绝对命中率。

## 路由行为占比

| 情况 | 条数 | 占比 |
|---|---|---|
| 完全跳过语义决策（0 次调用） | 15 | 62% |
| 使用单条语义分支 | 3 | 12% |
| 触发分叉（beam ≥ 2） | 6 | 25% |

平均每问语义调用 **0.86** 次，路由成本 **$0.000012**/问，路由耗时 **515ms**/问。

## 路由改变了结果的题（off vs active）

**没有一条案例的结果因路由而改变。**

这是真实结论，不是缺陷：绝大多数问题的槽位由确定性规则确定，路由根本不介入；介入的那些题目里，保守路径与合并去重也常常得到同样的首选。要让「路由是否值得」有统计意义，需要更多真正的模糊问法，而不是更多的明确问题。

## 逐条明细

| 案例 | 类型 | 模式 | 命中@1 | 命中@5 | 调用 | 分叉 | 分支数 | 槽位 | 状态 | 路由说明 |
|---|---|---|---|---|---|---|---|---|---|---|
| r01 | explicit | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r02 | explicit | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r03 | fallback | off | 对 | 对 | 0 | 否 | 1 | subject=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r04 | single_ask | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、field=✓、field_keys=✗ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r05 | ambiguity | off | 错 | 对 | 0 | 否 | 1 | subject=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r06 | nokey | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r07 | explicit | off | 对 | 对 | 0 | 否 | 1 | subject=✓、patch=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r08 | crosspatch | off | 对 | 对 | 0 | 否 | 1 | subject=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r01 | explicit | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r02 | explicit | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r03 | fallback | off | 对 | 对 | 0 | 否 | 1 | subject=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r04 | single_ask | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、field=✓、field_keys=✗ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r05 | ambiguity | off | 错 | 对 | 0 | 否 | 1 | subject=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r06 | nokey | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r07 | explicit | off | 对 | 对 | 0 | 否 | 1 | subject=✓、patch=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r08 | crosspatch | off | 对 | 对 | 0 | 否 | 1 | subject=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r01 | explicit | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r02 | explicit | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r03 | fallback | off | 对 | 对 | 0 | 否 | 1 | subject=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r04 | single_ask | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、field=✓、field_keys=✗ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r05 | ambiguity | off | 错 | 对 | 0 | 否 | 1 | subject=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r06 | nokey | off | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r07 | explicit | off | 对 | 对 | 0 | 否 | 1 | subject=✓、patch=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r08 | crosspatch | off | 对 | 对 | 0 | 否 | 1 | subject=✓ | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| r01 | explicit | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r02 | explicit | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r03 | fallback | shadow | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 E 置信度 0.15 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| r04 | single_ask | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、field=✓、field_keys=✗ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r05 | ambiguity | shadow | 错 | 对 | 2 | 否 | 2 | subject=✓ | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| r06 | nokey | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r07 | explicit | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r08 | crosspatch | shadow | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.16 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| r01 | explicit | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r02 | explicit | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r03 | fallback | shadow | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.16 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| r04 | single_ask | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、field=✓、field_keys=✗ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r05 | ambiguity | shadow | 错 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.53 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| r06 | nokey | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r07 | explicit | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r08 | crosspatch | shadow | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.13 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| r01 | explicit | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r02 | explicit | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r03 | fallback | shadow | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.16 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| r04 | single_ask | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、field=✓、field_keys=✗ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r05 | ambiguity | shadow | 错 | 对 | 2 | 否 | 2 | subject=✓ | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| r06 | nokey | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r07 | explicit | shadow | 对 | 对 | 0 | 否 | 1 | subject=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| r08 | crosspatch | shadow | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.13 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、6 |
| r01 | explicit | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r02 | explicit | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r03 | fallback | active | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.17 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| r04 | single_ask | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、field=✓、field_keys=✗ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r05 | ambiguity | active | 错 | 对 | 2 | 否 | 2 | subject=✓ | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| r06 | nokey | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r07 | explicit | active | 对 | 对 | 0 | 否 | 1 | subject=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r08 | crosspatch | active | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.12 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| r01 | explicit | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r02 | explicit | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r03 | fallback | active | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.18 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| r04 | single_ask | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、field=✓、field_keys=✗ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r05 | ambiguity | active | 错 | 对 | 2 | 否 | 2 | subject=✓ | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| r06 | nokey | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r07 | explicit | active | 对 | 对 | 0 | 否 | 1 | subject=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r08 | crosspatch | active | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.19 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| r01 | explicit | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r02 | explicit | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r03 | fallback | active | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.17 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| r04 | single_ask | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓、field=✓、field_keys=✗ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r05 | ambiguity | active | 错 | 对 | 2 | 否 | 2 | subject=✓ | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| r06 | nokey | active | 对 | 对 | 0 | 否 | 1 | subject=✓、ability=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r07 | explicit | active | 对 | 对 | 0 | 否 | 1 | subject=✓、patch=✓ | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| r08 | crosspatch | active | 对 | 对 | 2 | 是 | 3 | subject=✓ | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.14 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |

## 测到了什么 / 没有证明什么

**测到了**

- 完全明确的问题确实 0 次语义调用（15/24 条），说明「只对未确定槽位提问」不是口号而是链路行为。
- **在本次案例集上，路由没有改变任何一条的结果**（off 与 active 都是 7.0/24，改善 0、损害 0）。这不是脚本故障：8 条里有 5 条根本不需要模型（规则已定），剩下 3 条里 2 条的唯一合理解读本来就能被检索命中，第 8 条（多技能都可能带 damage 行）在语料上无法判定，
  两种口径都答不对。也就是说：**没有测到分层路由带来的命中率提升**。
  分叉与合并确实发生了（见下表明细），并且分叉题目的候选集合确实包含更多行 —— 机制在运行，只是在这些题目上没有转化为更好的首选。
- `shadow` 与 `off` 结果完全一致，说明 shadow 只观察不干预。
- 无 key 时链路完整可用，降级会写进轨迹与阶段账本。

**没有证明**

- **没有证明分层路由提高命中率。** 本次 live 口径下改善 0 条；此前用案例文件里手写分布跑出的 `5/8 → 8/8` 是 **fixture 口径**的结果，换成真实模型后没有复现 —— 那组数字只能说明链路能按预期分支，不能说明模型会选对。
- **样本量小**：能触发路由的只有 9 条，`0/9` 这种比例不能外推成「路由把准确率提高了 37%」。它证明的是**机制有效**，不是**收益幅度**。
- 没有证明置信门槛（0.35 / 0.60）与 beam_ratio（0.5）是最优值。它们是在这些案例上权衡后的保守取值，样本量不足以支撑「最优阈值」这种说法。
- 没有覆盖「路由把本来对的问题改错」的场景：本次损害 0 条，但这只能说明这批案例里没出现，不能说明不会出现。
