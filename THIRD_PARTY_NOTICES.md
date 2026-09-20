# 第三方组件与数据来源

本项目只包含自己的代码与统计结果；下列外部资源按各自条款使用，**运行时数据不进仓库**。

## 数据

| 来源 | 用途 | 说明 |
|---|---|---|
| [国服《英雄联盟》版本更新公告](https://lol.qq.com/)（腾讯） | 主语料：英雄、装备、系统数值改动与公告叙述 | 版权归腾讯/Riot Games 所有。抓取结果仅存本地 `data/patch/`（`raw/`、`knowledge.json` 已被 `.gitignore` 排除），仓库只保留解析脚本、统计与测试用的小样例。 |
| [Riot Data Dragon](https://developer.riotgames.com/docs/lol#data-dragon) | 英雄/装备中英文名与标签别名表 | 按 Riot 的 Data Dragon 使用条款使用，仅用于名称归一化。 |

## 模型与运行时

| 组件 | 许可 | 说明 |
|---|---|---|
| [Qwen2.5-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF) | Apache-2.0 | 本地生成模型（当前仅用于受限归纳） |
| [Qwen3-Embedding-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF) | Apache-2.0 | 本地向量模型 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | MIT | 模型推理运行时（固定 b10853） |

`scripts/download_models.py` 与 `scripts/download_runtime.py` 会按 SHA-256 校验下载内容。

## 外部 API

| 服务 | 用途 | 说明 |
|---|---|---|
| [TypeSafe System One（Jev）](https://docs.typesafe.ai/) | 候选重排、类别判定、证据一致性校验 | 可选；需要 `TYPESAFE_API_KEY`，无 key 时自动降级为纯检索排序。 |

## 免责声明

本项目是个人实验，与腾讯、Riot Games、TypeSafe AI 均无关联，不冒充官方数据服务；
公告数值以官方原文为准，本项目的解析结果可能存在漏项（见 `docs/解析覆盖率.md`）。
