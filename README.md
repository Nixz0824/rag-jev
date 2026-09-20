# RAG Jev

国服《英雄联盟》版本更新公告的本地 RAG 问答，加上一个 **Jev（TypeSafe System One）决策层**。

回答里的数字全部来自官方公告的结构化抽取结果，不经过生成模型；Jev 只负责「在候选里选哪一个」这类可校验的判断。

- 语料：国服官方版本更新公告（lol.qq.com）最近 10 个版本，约 1170 条（约 500 条数值改动 + 约 670 段公告叙述）
- 检索：版本/对象/字段元数据过滤 → BM25 + Qwen3-Embedding 向量 → RRF
- 决策：Jev 逐候选打分（noul）+ 相对选择（choice）做重排，失败时自动退回纯检索排序
- 自检：每条回答再用 Jev 核对一次「这条改动是否被证据支持」，支持度低时显式提示（`RAGJEV_SELF_CHECK=0` 可关闭）
- 生成：数值走模板渲染；叙述类问题只做归纳，不产生数字
- 运行：全部本地（llama.cpp + Qwen GGUF），只有 Jev 需要外部 API key

## 快速开始

```powershell
# 1. 依赖：Python 3.12+ 与 numpy
python -m pip install numpy

# 2. 模型（约 1.7 GB，脚本按 SHA-256 校验）
python scripts/download_runtime.py
python scripts/download_models.py

# 3. 语料（需要联网：Data Dragon + 国服公告）
python scripts/build_aliases.py
python scripts/ingest_qq.py --count 10

# 4. 启动（首次会构建向量索引，约 1–3 分钟）
python launch.py
# 浏览器打开 http://127.0.0.1:18890
```

停止：`python launch.py --stop`，或运行 `停止服务.cmd`。

### Jev（可选）

```powershell
$env:TYPESAFE_API_KEY = "sk-..."   # 或写入 runtime/jev-key.txt
```

没有 key 时系统照常工作，只是不做重排（`/api/health` 会显示 `jev.enabled=false`）。

## 能问什么

| 问法 | 行为 |
|---|---|
| `26.17 亚索改了什么` | 该版本该英雄的全部结构化改动 |
| `16.17 岚切怎么调整的` | Data Dragon 版号自动换算到公告版号 26.17 |
| `26.17 有哪些英雄被削弱` | 该版本改动清单（默认只看召唤师峡谷） |
| `26.17 薇恩 W 真实伤害是多少` | 先按字段过滤，再由 Jev 选出最匹配的一条 |
| `26.18 阿卡丽改了什么` | 该版本没有记录时，提示其它版本里有记录 |
| `经典模式亚索改了什么` | 按模式过滤（经典模式/大乱斗/竞技场） |

## 命令

```powershell
python -m unittest discover -s tests          # 59 项，不需要模型与网络
python scripts/ingest_qq.py --offline         # 用已缓存公告重建语料
python scripts/ingest_qq.py --count 10 --pages 40
python scripts/build_aliases.py --offline     # 重建英雄/装备别名表
python scripts/evaluate_patch.py              # 四组检索口径 A/B（需模型在跑；Jev 需要 key）
python scripts/evaluate_parse.py              # 查询理解盲测（不需要模型）
python scripts/calibrate.py --report          # 门槛测量（结论见 docs/门槛校准.md）
python scripts/ingest_en.py --offline         # 与英文公告对照核验
python scripts/review_feedback.py             # 复核页面反馈，产出 docs/反馈复核.md
# 盲测（先按 docs/盲测集提示词.md 出题）：
python scripts/evaluate_patch.py --cases tests\cases\blind_cases.json --out docs\盲测报告.md
```

产出：`data/patch/knowledge.json`（语料）、`data/patch/patch_map.json`（版本映射）、
`data/patch/aliases.json`（别名）、`docs/解析覆盖率.md`（解析质量）、`docs/评测报告.md`（A/B）、
`docs/查询理解.md`（盲测）、`docs/门槛校准.md`、`docs/对照核验.md`。

## 当前指标

| 指标 | 结果 | 口径 |
|---|---|---|
| **盲测数值正确率（20 条单点）** | **20/20**（界面选中版本）· 19/20（无版本上下文） | **不同源**：由 Codex 按官方公告出题，未接触语料 |
| **盲测汇总 / 拒答** | 8/8 · 10/10 | 同上，38 题盲测集 |
| 盲测命中@1 / @5（单点） | 19/20 · 20/20 | 同上 |
| 查询理解盲测（48 条） | 48/48 | 期望只看解析结果，与语料不同源 |
| 陷阱题（18 条） | 18/18 | 行为规则定义，与语料不同源 |
| 定点命中@1（15 条） | 14/15（混合）· **15/15**（混合+Jev） | 同源 |
| 单点命中@1（28 条） | 28/28（四口径一致） | 同源 |
| 汇总含预期对象（4 条） | 4/4 | 同源 |
| 无记录/越界拒答（7 条） | 7/7 | 同源 |
| 回答自检（Jev noul） | 47/47 条回答最低支持度 ≥ 0.5 | Jev 口径 |

盲测报告：`docs/盲测报告-带版本上下文.md`（界面选中版本，贴近真实使用）与 `docs/盲测报告.md`（只用问题文本）。
38 题盲测集由 Codex 依 `docs/盲测集提示词.md` 生成，出题时禁止读语料——这是唯一能支撑"准确率"说法的测量。

元数据过滤之后候选通常只有 1—6 条，三种排序方式因此打平；Jev 的差异只出现在
「问题用词与字段标签不一致」的定点问题上（案例 h05）。平均成本 $0.000038/次。
范围门槛经测量后**不采用**：领域内与越界问题的相似度重叠（0.5458 / 0.5980）。

## 数据边界（请按此引用本项目）

- 主语料是**国服官方公告正文**，覆盖英雄、装备与系统数值改动；皮肤、炫彩、云顶、模式机制不在抽取范围。
- 公告里没有写的**版本内热修**不在语料中，系统会用明确措辞提示，而不是猜。
- 解析可能漏项：`docs/解析覆盖率.md` 记录每个版本的解析量与未识别字段；叙述行只用于检索，不参与数值回答。
- 国服公告数值与其它服务器可能不同，本项目以国服为准，不做跨服务器断言。

## 版权

公告原文只保存在本地 `data/patch/`（已被 `.gitignore` 排除），仓库里只有抓取解析脚本、统计与少量测试样例。
数据来源与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 目录

```
config.py           端口与路径的单一来源
engine.py           BM25 + 向量 + RRF 检索内核与门槛
patch_engine.py     版本解析、元数据过滤、答案渲染（含 Jev 接入点）
patch_schema.py     字段键、箭头/技能行解析、句子的唯一定义处
jev.py              TypeSafe System One 客户端（重排 / 分类 / 校验）
server.py           本地 HTTP API 与静态页面
launch.py           三进程监督（Windows Job Object，退出即回收）
scripts/            ingest_qq / build_aliases / download_models / download_runtime
tests/              59 项单元测试 + 手写样例语料
web/                单页工作台
```
