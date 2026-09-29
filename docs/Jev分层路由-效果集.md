# Jev 分层路由评测

生成时间：2026-09-29 19:44　案例：40 条　来源：routing_effect_cases.json　模式：live　检索：bm25　检索口径：BM25（不依赖本地向量服务，便于复现）

> **口径**：live：调用真实 TypeSafe API，槽位判断按案例期望评分。
> 命中判据是「引擎实际返回的证据列表里第 1 / 前 5 条是否为该案例的期望行」，期望行由 `expect.rows` 从语料里解析，数值取自官方公告。
> 本报告同时给出负面结果：路由没有改变结果的题数、以及路由改错的题数。

## 三口径对照

| 口径 | 命中@1 | 命中@5 | 槽位正确 | 路由调用次数 | 路由成本 | 路由耗时 | 分叉题数 | 平均总耗时 |
|---|---|---|---|---|---|---|---|---|
| off | 25/40 | 40/40 | —（未提问） | 0 | $0.000000 | 0ms | 0/40 | 0ms |
| shadow | 25/40 | 40/40 | —（未提问） | 210 | $0.002591 | 106656ms | 21/40 | 903ms |
| active | 28.3/40 | 40/40 | —（未提问） | 210 | $0.002532 | 105468ms | 22/40 | 894ms |

「槽位正确」只统计**路由器真的提问过**的案例：`routing=off` 不提问，低置信槽位既不算答对也不算答错，因此该列显示「未提问」，而不是把「没问」记成「答错」。

**重复 3 次**（模型有随机性，单次结果不足以支撑结论）。每轮命中@1：

| 口径 | 每轮命中@1 | 均值 |
|---|---|---|
| off | 25、25、25 | 25.0/40 |
| shadow | 25、25、25 | 25.0/40 |
| active | 28、28、29 | 28.3/40 |

**跨轮次翻转的案例**（唯一能说明路由是否起作用的证据）：
- `e27`「e27」：active 命中 1/3 轮（off 每轮都命中）。这是**语料本身无法判定**的问法，模型每轮给出的读法不同，所以两种口径的结果都会随轮次变化。

**逐轮不稳定的案例**（含 off 侧的波动；重复次数越多越容易暴露）：

| 案例 | off 各轮 | active 各轮 | 说明 |
|---|---|---|---|
| e27 | — | 错错对 | active 侧自身不稳定 |

自身不稳定的案例，其 off/active 差异不能单独算作路由的功劳或责任，必须结合逐轮数据一起看。

**改变结果的案例数：5 条**（净改善 4 条、净损害 1 条；按轮次累计的条目数为 14，重复 3 次时会被放大 3 倍）。

| 案例 | 问题 | off 命中轮次 | active 命中轮次 | 方向 |
|---|---|---|---|---|
| e27 | 26.14 杰斯切换到近战姿态时拿到的额外双抗，各级改成多少了 | 2/2 | 0/2 | 损害 |
| e12 | 15.17 艾瑞莉娅那个蓄力格挡的斩击蓄满后，法强加成调到多少 | 0/3 | 3/3 | 改善 |
| e17 | 15.18 岩雀撒下石片再被踩中引爆时，基础伤害降成多少了 | 0/3 | 3/3 | 改善 |
| e18 | 15.19 布兰德从地面炸开的那片火焰，伤害法强加成改到多少了 | 0/3 | 3/3 | 改善 |
| e32 | 26.16 波比身边那个防护力场给的双抗，现在是多少了 | 0/3 | 3/3 | 改善 |

（逐轮累计的条目数会被重复次数放大，本表按案例统计，共 5 条案例发生过改变。）

哪些题两种口径都答不对、以及原因归属，跑 `python scripts/attribute_routing_errors.py --cases <案例文件>`。

## 两种口径的解题分布

| 情况 | 条数 | 占比 |
|---|---|---|
| 两种口径都答对 | 25 | 62% |
| **只有 active 答对（路由的贡献）** | **4** | 10% |
| **只有 off 答对（路由的损害）** | **0** | 0% |
| 两种口径都答不对 | 11 | 28% |

这张表比命中率更直白：路由的价值是第三行，代价是第四行。
两行都不为零时，结论必须同时给出两个数字，不能只报净提升。

- `shadow` 与 `active` 的调用统计相同（各 210 条案例记录到调用）：shadow 照常做语义判断，只是不把结果用于检索。因此两者的差异只能来自「是否使用路由」，而不是「是否运行路由」。
- 本次运行实际发起的语义调用：**210 次**（下表按案例累计为 210 次）。两者相等：本次每个案例都真实发起了调用。
- 每问平均语义调用 **1.75** 次（只按需要判断的问题计，分母 40.0 条）。
- 检索口径为 BM25，与生产默认的 hybrid 会有细微差异；这里比较的是路由带来的增量，不是绝对命中率。

## 路由行为占比

| 情况 | 条数 | 占比 |
|---|---|---|
| 完全跳过语义决策（0 次调用） | 15 | 12% |
| 使用单条语义分支 | 39 | 32% |
| 触发分叉（beam ≥ 2） | 66 | 55% |

平均每问语义调用 **1.75** 次，路由成本 **$0.000021**/问，路由耗时 **879ms**/问。

## 路由改变了结果的题（off vs active）

| 案例 | 问题 | off 命中@1 | active 命中@1 | off 找到 | active 找到 | 分叉 |
|---|---|---|---|---|---|---|
| e12 | 15.17 艾瑞莉娅那个蓄力格挡的斩击蓄满后，法强加成调到多少 | 错 | 对 | 是 | 是 | 是 |
| e17 | 15.18 岩雀撒下石片再被踩中引爆时，基础伤害降成多少了 | 错 | 对 | 是 | 是 | 否 |
| e18 | 15.19 布兰德从地面炸开的那片火焰，伤害法强加成改到多少了 | 错 | 对 | 是 | 是 | 是 |
| e27 | 26.14 杰斯切换到近战姿态时拿到的额外双抗，各级改成多少了 | 对 | 错 | 是 | 是 | 是 |
| e32 | 26.16 波比身边那个防护力场给的双抗，现在是多少了 | 错 | 对 | 是 | 是 | 是 |
| e12 | 15.17 艾瑞莉娅那个蓄力格挡的斩击蓄满后，法强加成调到多少 | 错 | 对 | 是 | 是 | 是 |
| e17 | 15.18 岩雀撒下石片再被踩中引爆时，基础伤害降成多少了 | 错 | 对 | 是 | 是 | 否 |
| e18 | 15.19 布兰德从地面炸开的那片火焰，伤害法强加成改到多少了 | 错 | 对 | 是 | 是 | 是 |
| e27 | 26.14 杰斯切换到近战姿态时拿到的额外双抗，各级改成多少了 | 对 | 错 | 是 | 是 | 是 |
| e32 | 26.16 波比身边那个防护力场给的双抗，现在是多少了 | 错 | 对 | 是 | 是 | 是 |
| e12 | 15.17 艾瑞莉娅那个蓄力格挡的斩击蓄满后，法强加成调到多少 | 错 | 对 | 是 | 是 | 是 |
| e17 | 15.18 岩雀撒下石片再被踩中引爆时，基础伤害降成多少了 | 错 | 对 | 是 | 是 | 否 |
| e18 | 15.19 布兰德从地面炸开的那片火焰，伤害法强加成改到多少了 | 错 | 对 | 是 | 是 | 是 |
| e32 | 26.16 波比身边那个防护力场给的双抗，现在是多少了 | 错 | 对 | 是 | 是 | 是 |

- 路由**改善**命中@1：12 条；路由**损害**命中@1：2 条。
- 路由**扩大召回**（原本找不到、现在找到）：0 条；路由**丢失召回**：0 条。

## 逐条明细

| 案例 | 类型 | 模式 | 命中@1 | 命中@5 | 调用 | 分叉 | 分支数 | 槽位 | 状态 | 路由说明 |
|---|---|---|---|---|---|---|---|---|---|---|
| e01 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e02 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e03 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e04 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e05 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e06 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e07 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e08 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e09 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e10 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e11 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e12 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e13 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e14 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e15 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e16 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e17 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e18 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e19 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e20 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e21 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e22 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e23 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e24 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e25 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e26 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e27 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e28 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e29 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e30 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e31 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e32 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e33 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e34 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e35 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e36 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e37 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e38 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e39 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e40 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e01 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e02 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e03 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e04 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e05 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e06 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e07 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e08 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e09 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e10 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e11 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e12 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e13 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e14 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e15 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e16 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e17 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e18 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e19 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e20 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e21 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e22 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e23 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e24 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e25 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e26 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e27 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e28 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e29 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e30 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e31 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e32 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e33 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e34 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e35 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e36 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e37 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e38 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e39 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e40 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e01 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e02 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e03 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e04 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e05 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e06 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e07 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e08 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e09 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e10 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e11 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e12 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e13 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e14 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e15 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e16 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e17 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e18 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e19 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e20 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e21 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e22 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e23 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e24 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e25 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e26 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e27 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e28 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e29 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e30 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e31 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e32 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | abstained | routing=off：完全使用确定性解析，不调用语义决策 |
| e33 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e34 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e35 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e36 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e37 | routing_effect | off | 错 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e38 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e39 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e40 | routing_effect | off | 对 | 对 | 0 | 否 | 1 | — | verify | routing=off：完全使用确定性解析，不调用语义决策 |
| e01 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e02 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e03 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e04 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e05 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.53 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e06 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.54 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e07 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.34 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e08 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e09 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.58 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e10 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.53 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e11 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.18 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e12 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.44 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e13 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e14 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e15 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.51 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e16 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.17 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e17 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e18 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：W（0.35） / 分层路由：判断 1 个层级、1 次调用、798ms / 分叉未触发：首选明显领先，只走 |
| e19 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.40 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e20 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.42 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e21 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e22 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.39 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e23 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.23 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e24 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e25 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e26 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.17 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e27 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.20 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e28 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e29 | routing_effect | shadow | 错 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e30 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.51 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e31 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.44 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e32 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：W（0.37） / 分层路由：判断 1 个层级、1 次调用、783ms / 分叉未触发：首选明显领先，只走 |
| e33 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e34 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.51 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e35 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e36 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e37 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.49 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e38 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e39 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e40 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.44 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e01 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e02 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e03 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e04 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e05 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.54 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e06 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.47 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e07 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.35 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e08 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e09 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.49 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e10 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.57 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e11 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.27 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e12 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.41 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e13 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e14 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e15 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.47 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e16 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.18 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e17 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e18 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：W（0.36） / 分层路由：判断 1 个层级、1 次调用、838ms / 分叉触发（beam=2）：存在 |
| e19 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.41 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e20 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：被动（0.39） / 分层路由：判断 1 个层级、1 次调用、884ms / 分叉未触发：首选明显领先，只 |
| e21 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e22 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.37 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e23 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.20 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、9 |
| e24 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e25 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e26 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.20 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e27 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.22 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e28 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e29 | routing_effect | shadow | 错 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e30 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.53 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e31 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.42 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e32 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.32 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e33 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.51 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e34 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.40 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e35 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e36 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e37 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.53 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e38 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e39 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e40 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.43 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e01 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e02 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e03 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e04 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e05 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.49 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e06 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.52 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e07 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：W（0.35） / 分层路由：判断 1 个层级、1 次调用、857ms / 分叉触发（beam=2）：存在 |
| e08 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e09 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.53 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e10 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e11 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.23 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e12 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.39 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e13 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e14 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e15 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.48 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e16 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.12 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e17 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e18 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：W（0.35） / 分层路由：判断 1 个层级、1 次调用、804ms / 分叉未触发：首选明显领先，只走 |
| e19 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.40 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e20 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.32 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e21 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e22 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：W（0.38） / 分层路由：判断 1 个层级、1 次调用、831ms / 分叉触发（beam=2）：存在 |
| e23 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.24 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e24 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e25 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e26 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.16 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e27 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.17 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e28 | routing_effect | shadow | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e29 | routing_effect | shadow | 错 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 / shadow：语义结果已记录，未影响实际检索 |
| e30 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.51 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e31 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.42 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e32 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.34 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e33 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.57 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e34 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.41 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e35 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e36 | routing_effect | shadow | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e37 | routing_effect | shadow | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.49 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e38 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e39 | routing_effect | shadow | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 / sh |
| e40 | routing_effect | shadow | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.38 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e01 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e02 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e03 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e04 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e05 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.55 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e06 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.49 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e07 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.33 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e08 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e09 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e10 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.57 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e11 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.29 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e12 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.42 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e13 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e14 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e15 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.49 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e16 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.19 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e17 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e18 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.37 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e19 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.41 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e20 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.36 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e21 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e22 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：Q（0.35） / 分层路由：判断 1 个层级、1 次调用、846ms / 分叉触发（beam=2）：存在 |
| e23 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.16 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e24 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e25 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e26 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.17 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e27 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.19 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e28 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e29 | routing_effect | active | 错 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e30 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.57 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e31 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.39 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e32 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.33 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e33 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.53 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e34 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.43 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e35 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e36 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e37 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.43 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e38 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e39 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e40 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.42 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e01 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e02 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e03 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e04 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e05 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.56 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e06 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.51 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e07 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.33 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e08 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e09 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.52 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e10 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.57 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e11 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.24 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e12 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.39 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e13 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e14 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e15 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.48 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e16 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.17 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e17 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e18 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.36 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e19 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.35 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e20 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.23 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e21 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e22 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.39 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e23 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.22 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e24 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e25 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e26 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.16 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、8 |
| e27 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.19 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e28 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e29 | routing_effect | active | 错 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e30 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e31 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.41 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e32 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：W（0.36） / 分层路由：判断 1 个层级、1 次调用、819ms / 分叉触发（beam=2）：存在 |
| e33 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.54 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e34 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.40 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e35 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e36 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e37 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.52 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e38 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e39 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e40 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.44 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e01 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e02 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e03 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e04 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e05 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.58 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e06 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.48 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e07 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability：W（0.36） / 分层路由：判断 1 个层级、1 次调用、854ms / 分叉触发（beam=2）：存在 |
| e08 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e09 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.52 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e10 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e11 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.19 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e12 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.42 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e13 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e14 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e15 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.54 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e16 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 被动 置信度 0.17 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、 |
| e17 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e18 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.41 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e19 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.38 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e20 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.35 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e21 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e22 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.38 未达单独采信门槛 0.60，另开一条 Q 分支 / 分层路由未新增调用：语义补全已确定 |
| e23 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 Q 置信度 0.22 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e24 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e25 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e26 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.15 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e27 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 R 置信度 0.16 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e28 | routing_effect | active | 对 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e29 | routing_effect | active | 错 | 对 | 0 | 否 | 1 | — | verify | 语义补全未触发：待判槽位已由确定性规则确定 / 分层路由未触发：所有层级都由确定性规则确定，无需分叉 |
| e30 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.59 未达单独采信门槛 0.60，另开一条 W 分支 / 分层路由未新增调用：语义补全已确定 |
| e31 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.39 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |
| e32 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | abstained | 语义补全：无采纳（1 个待定槽位，1 次调用） / 层级 ability 首选 W 置信度 0.29 偏低，保留为分叉而不是唯一路径 / 分层路由：判断 1 个层级、1 次调用、7 |
| e33 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.46 未达单独采信门槛 0.60，另开一条 E 分支 / 分层路由未新增调用：语义补全已确定 |
| e34 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.46 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e35 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e36 | routing_effect | active | 错 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e37 | routing_effect | active | 错 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.54 未达单独采信门槛 0.60，另开一条 被动 分支 / 分层路由未新增调用：语义补全已确 |
| e38 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e39 | routing_effect | active | 对 | 对 | 2 | 否 | 2 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / 分层路由未新增调用：语义补全已确定全部层级，整理为 1 条检索读法 / 分叉未触发：首选明显领先，只走单一路径 |
| e40 | routing_effect | active | 对 | 对 | 2 | 是 | 3 | — | verify | 语义补全：ability（1 个待定槽位，1 次调用） / ability 置信度 0.41 未达单独采信门槛 0.60，另开一条 R 分支 / 分层路由未新增调用：语义补全已确定 |

## 测到了什么 / 没有证明什么

**测到了**

- 完全明确的问题确实 0 次语义调用（15/120 条），说明「只对未确定槽位提问」不是口号而是链路行为。
- 在能触发路由的题目上，命中@1 由 25.0/120 变为 28.333333333333332/120：改善 12 条、损害 2 条，召回扩大 0 条、丢失 0 条。
  分叉与合并确实发生了（见下表明细），并且分叉题目的候选集合确实包含更多行 —— 机制在运行，只是在这些题目上没有转化为更好的首选。
- `shadow` 与 `off` 结果完全一致，说明 shadow 只观察不干预。
- 无 key 时链路完整可用，降级会写进轨迹与阶段账本。

**没有证明**

- **没有证明分层路由提高命中率。** 本次 live 口径下改善 0 条；此前用案例文件里手写分布跑出的 `5/8 → 8/8` 是 **fixture 口径**的结果，换成真实模型后没有复现 —— 那组数字只能说明链路能按预期分支，不能说明模型会选对。
- **样本量小**：能触发路由的只有 105 条，`12/105` 这种比例不能外推成「路由把准确率提高了 37%」。它证明的是**机制有效**，不是**收益幅度**。
- 没有证明置信门槛（0.35 / 0.60）与 beam_ratio（0.5）是最优值。它们是在这些案例上权衡后的保守取值，样本量不足以支撑「最优阈值」这种说法。
- 没有覆盖「路由把本来对的问题改错」的场景：本次损害 0 条，但这只能说明这批案例里没出现，不能说明不会出现。
