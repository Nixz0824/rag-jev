"""Corpus statistics: per-patch table, distributions and a before/after comparison.

Reads data/patch/knowledge.json + patch_map.json and writes docs/语料统计.md.
Nothing here talks to the network or the models, so it can run any time.

Usage:
    python scripts/corpus_stats.py
    python scripts/corpus_stats.py --recent 10   # size of the "before" window
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DOCS, PATCH_DATA  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODE_LABELS = {"rift": "召唤师峡谷", "classic": "经典模式", "aram": "海克斯大乱斗", "arena": "斗魂竞技场", "other": "其它"}
TYPE_LABELS = {"champion": "英雄", "item": "装备", "system": "系统", "mode": "模式"}


def patch_sort(value: str) -> tuple[int, int]:
    major, minor = value.split(".")[:2]
    return int(major), int(minor)


def summarise(chunks: list[dict]) -> dict:
    structured = [row for row in chunks if row["field_key"] != "narrative"]
    return {
        "patches": len({row["patch"] for row in chunks}),
        "chunks": len(chunks),
        "structured": len(structured),
        "narrative": len(chunks) - len(structured),
        "unknown": sum(1 for row in structured if row["field_key"] == "unknown"),
        "limited": sum(1 for row in structured if row["confidence"] == "limited"),
        "champions": len({row["subject"] for row in structured if row["type"] == "champion"}),
        "items": len({row["subject"] for row in structured if row["type"] == "item"}),
        "abilities": sum(1 for row in structured if row.get("ability")),
    }


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="生成语料统计表")
    parser.add_argument("--recent", type=int, default=10, help="对比窗口：最近多少个版本")
    args = parser.parse_args()

    knowledge = json.loads((PATCH_DATA / "knowledge.json").read_text(encoding="utf-8"))
    patch_map = json.loads((PATCH_DATA / "patch_map.json").read_text(encoding="utf-8"))["patches"]
    chunks = knowledge["chunks"]

    patches = sorted({row["patch"] for row in chunks}, key=patch_sort)
    recent = patches[-args.recent :]

    per_patch = []
    for patch in patches:
        rows = [row for row in chunks if row["patch"] == patch]
        stat = summarise(rows)
        per_patch.append(
            [
                patch,
                patch_map.get(patch, {}).get("ddragon", "") or "—",
                patch_map.get(patch, {}).get("date", "") or "—",
                str(stat["chunks"]),
                str(stat["structured"]),
                str(stat["narrative"]),
                str(stat["unknown"]),
                str(stat["champions"]),
                str(stat["items"]),
            ]
        )

    all_stat = summarise(chunks)
    recent_stat = summarise([row for row in chunks if row["patch"] in set(recent)])

    sections = collections.Counter(row["section"] for row in chunks if row["field_key"] != "narrative")
    field_keys = collections.Counter(row["field_key"] for row in chunks if row["field_key"] != "narrative")
    modes = collections.Counter(row["mode"] for row in chunks)
    types = collections.Counter(row["type"] for row in chunks if row["field_key"] != "narrative")

    lines = [
        "# 语料统计",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　来源：`data/patch/knowledge.json`（由 `scripts/ingest_qq.py` 生成）",
        "",
        "> 语料本身（含公告正文）不入仓库，这里只发布统计口径；任何数字都能用同一脚本复现。",
        "",
        "## 一、扩容前后对比",
        "",
    ]
    lines += table(
        ["指标", f"最近 {args.recent} 个版本（{recent[0]}—{recent[-1]}）", f"全部 {len(patches)} 个版本（{patches[0]}—{patches[-1]}）", "倍数"],
        [
            ["版本数", str(recent_stat["patches"]), str(all_stat["patches"]), f"×{all_stat['patches'] / recent_stat['patches']:.1f}"],
            ["chunk 总数", str(recent_stat["chunks"]), str(all_stat["chunks"]), f"×{all_stat['chunks'] / recent_stat['chunks']:.2f}"],
            ["结构化数值改动", str(recent_stat["structured"]), str(all_stat["structured"]), f"×{all_stat['structured'] / recent_stat['structured']:.2f}"],
            ["公告叙述段", str(recent_stat["narrative"]), str(all_stat["narrative"]), f"×{all_stat['narrative'] / recent_stat['narrative']:.2f}"],
            ["英雄（去重）", str(recent_stat["champions"]), str(all_stat["champions"]), f"×{all_stat['champions'] / recent_stat['champions']:.2f}"],
            ["装备（去重）", str(recent_stat["items"]), str(all_stat["items"]), f"×{all_stat['items'] / recent_stat['items']:.2f}"],
            ["带技能归属的改动", str(recent_stat["abilities"]), str(all_stat["abilities"]), f"×{all_stat['abilities'] / recent_stat['abilities']:.2f}"],
            ["字段未归一的行", str(recent_stat["unknown"]), str(all_stat["unknown"]), f"×{all_stat['unknown'] / recent_stat['unknown']:.2f}"],
        ],
    )
    lines += [
        "",
        "## 二、逐版本明细",
        "",
    ]
    lines += table(
        ["版本", "Data Dragon", "公告日期", "chunk", "结构化", "叙述", "字段未归一", "英雄", "装备"],
        per_patch,
    )
    lines += [
        "",
        "## 三、结构化改动的字段分布",
        "",
    ]
    lines += table(
        ["字段键", "条数", "占比"],
        [[key, str(count), f"{count / all_stat['structured']:.1%}"] for key, count in field_keys.most_common(18)],
    )
    lines += [
        "",
        "## 四、分区与模式分布",
        "",
    ]
    lines += table(
        ["公告分区", "条数"],
        [[name or "未分区", str(count)] for name, count in sections.most_common(12)],
    )
    lines += [""]
    lines += table(
        ["模式", "chunk", "其中结构化"],
        [
            [
                MODE_LABELS.get(mode, mode),
                str(count),
                str(sum(1 for row in chunks if row["mode"] == mode and row["field_key"] != "narrative")),
            ]
            for mode, count in modes.most_common()
        ],
    )
    lines += [""]
    lines += table(
        ["对象类型", "结构化条数"],
        [[TYPE_LABELS.get(kind, kind), str(count)] for kind, count in types.most_common()],
    )
    lines += [
        "",
        "## 五、解析质量口径",
        "",
        f"- 结构化占比：**{all_stat['structured'] / all_stat['chunks']:.1%}**（其余是公告叙述，只参与检索与「为什么改」回显）",
        f"- 字段归一率：**{1 - all_stat['unknown'] / all_stat['structured']:.1%}**（未归一的行仍保留原文，但不会被字段过滤命中）",
        f"- 带技能归属的结构化改动：**{all_stat['abilities']}/{all_stat['structured']}**"
        f"（{all_stat['abilities'] / all_stat['structured']:.1%}），其余是基础属性或系统条目",
        f"- `confidence=limited` 的行：**{all_stat['limited']}** 条（技能字段缺少技能归属等不确定情形）",
        "",
        "## 六、版本号的两套写法",
        "",
        "2025 赛季 Riot 改用赛季前缀（25.x），Data Dragon 仍从 10 计数（15.x），国服公告两套都用过，"
        "因此语料里同时存在 `15.13`—`15.24` 与 `25.10`、`25.11`、`25.15`。",
        "`scripts/ingest_qq.py` 的 `ddragon_map()` 与 `patch_engine.canonical_patch()` 都会先按原样匹配，"
        "再尝试 ±10 的换算，所以用户输入 `16.17`、`26.17`、`15.13` 都能落到正确版本。",
        "",
    ]

    DOCS.mkdir(exist_ok=True)
    (DOCS / "语料统计.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"版本 {all_stat['patches']} 个｜chunk {all_stat['chunks']}（结构化 {all_stat['structured']} / 叙述 {all_stat['narrative']}）"
          f"｜英雄 {all_stat['champions']}｜装备 {all_stat['items']}")
    print(f"最近 {args.recent} 版：chunk {recent_stat['chunks']}（结构化 {recent_stat['structured']}）")
    print(f"报告：{DOCS / '语料统计.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
