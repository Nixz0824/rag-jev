# AGENTS.md

给在本仓库工作的 AI agent 的常驻约定。动手前先读 `README.md` 与 `docs/交接文档.md`。

## 这是什么

「RAG Jev」：把国服《英雄联盟》版本更新公告抽成结构化条目，做版本感知检索；
Jev（TypeSafe System One）只做「候选里的相对选择 / 相关性打分 / 声称与证据是否一致」，
不生成文本。回答里的数字必须来自语料，绝不由模型产生。

## 环境（本机固定）

| 项 | 值 |
|---|---|
| 项目根 | 本仓库根目录 |
| Python | `python`（3.12.14 + numpy 2.3.5） |
| 端口 | 18890 应用 / 18891 聊天模型 / 18892 向量模型（只绑 127.0.0.1） |
| 模型 | `models/`（本机下载，或用 `RAGJEV_MODELS` 指向外部模型目录） |
| Key | `TYPESAFE_API_KEY`（环境变量或 `runtime/jev-key.txt`），只检查是否存在，不输出值 |

## 常用命令

```powershell
python -m unittest discover -s .\tests      # 59 项，不需要模型和网络
python .\scripts\ingest_qq.py --offline     # 用缓存重建语料
python .\launch.py --no-open                # 启动三进程
python .\launch.py --stop                   # 停止
```

## 必须遵守

1. **数字只能来自语料**：答案渲染集中在 `patch_engine._render`，不要改成让模型写数值。
2. **改语料必须重跑 `ingest_qq.py` 并更新 `docs/解析覆盖率.md`**；未识别字段变多要解释原因。
3. **解析规则只写在 `patch_schema.py`**：字段键、箭头、技能行、句子模板都在那里，解析脚本与引擎共用，禁止各写一套。
4. **新增行为必须带测试**：`tests/` 是唯一验收依据，59 项必须全绿。
5. **Jev 降级不能静默**：任何 `jev.*` 调用失败都要写进 `session["trace"]`。
6. **公告原文不进仓库**：`data/patch/raw/`、`data/patch/knowledge.json` 已被忽略，不要手动加回。
7. 提交用分层小提交，消息写清「改了什么 + 为什么」，署名 `Nixz0824`。

## 已知坑

| 坑 | 事实 |
|---|---|
| 箭头两种写法 | 公告里既有 `&rArr;`（⇒）也有 `&rarr;`（→），`patch_schema.ARROW_RE` 同时接受；漏了会整版解析成 0 条 |
| Data Dragon 名称字段可能互换 | 本机数据里 `champion.name` 是称号、`title` 才是名字；别名表按两者都建键，并靠 `subject_en` 归到语料里的写法 |
| 伪对象 | 模式分区里的 `英雄/装备/强化符文` 会变成 subject，必须排除出别名表，否则「有哪些英雄被削弱」会被解析成查询「英雄」 |
| 粗体标记 | 解析时用 `\ue000` 标记 `<strong>`，非标题行必须去掉，否则会混进字段名与正文 |
| 公告标题不统一 | 有的版本是 `26.13版本更新公告`，有的是 `6月25日凌晨1点停机版本更新公告`，需要从文章里回读版本号 |
| `--api-key-file` 中文路径 | llama-server 用窄字符 API 打开该文件，绝对路径含中文必然失败，必须传相对路径 `runtime/model-key.txt` |
| 子进程孤儿 | 启动器依赖 Windows Job Object（KILL_ON_JOB_CLOSE），不要改成普通 `terminate()` |
| 索引指纹 | 指纹 = 语料字节 + 模型标识 + 检索指令；语料一变索引自动重建，不要手删 `data/patch/index.npz` |
