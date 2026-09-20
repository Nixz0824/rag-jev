"""Calibrate the scope gate from tests/cases/gate_cases.json.

The gate decides between "I need a name to continue" and "this is off-topic", so
it is thresholded on the highest score an off-topic question can reach versus the
lowest a domain question reaches. Writes data/patch/thresholds.json.

Usage:
    python scripts/calibrate.py            # scan and write thresholds
    python scripts/calibrate.py --report   # only print the table
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import PATCH_DATA, TESTS  # noqa: E402
from patch_engine import PatchRetriever  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES = TESTS / "cases" / "gate_cases.json"


def scores(retriever: PatchRetriever, text: str) -> tuple[float, float]:
    hits = retriever.search(text, k=5)
    if not hits:
        return 0.0, 0.0
    return max(hit["similarity"] for hit in hits), max(hit["bm25"] for hit in hits)


def main() -> int:
    parser = argparse.ArgumentParser(description="校准范围门槛")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    retriever = PatchRetriever()
    retriever.build()

    rows = []
    for kind in ("in_scope", "out_of_scope"):
        for question in cases[kind]:
            similarity, bm25 = scores(retriever, question)
            rows.append({"kind": kind, "question": question, "similarity": similarity, "bm25": bm25})

    inside = [row for row in rows if row["kind"] == "in_scope"]
    outside = [row for row in rows if row["kind"] == "out_of_scope"]
    low_in = min(row["similarity"] for row in inside)
    high_out = max(row["similarity"] for row in outside)
    bm25_in = min(row["bm25"] for row in inside)
    bm25_out = max(row["bm25"] for row in outside)

    print(f"{'类型':<10}{'相似度':>9}{'BM25':>9}  问题")
    for row in sorted(rows, key=lambda item: item["similarity"]):
        print(f"{row['kind']:<10}{row['similarity']:>9.4f}{row['bm25']:>9.3f}  {row['question']}")
    print()
    print(f"范围内最低相似度 {low_in:.4f} ／ 越界最高相似度 {high_out:.4f}")
    print(f"范围内最低 BM25 {bm25_in:.3f} ／ 越界最高 BM25 {bm25_out:.3f}")

    if low_in > high_out:
        similarity = round((low_in + high_out) / 2, 3)
        verdict = f"相似度空隙存在，取 {similarity}（空隙 {high_out:.4f}—{low_in:.4f}）"
        adopted = True
    else:
        similarity = None
        verdict = "相似度上没有空隙：范围内与越界重叠，**不采用相似度门槛**"
        adopted = False
    print(verdict)
    if bm25_in > bm25_out:
        bm25_limit = round((bm25_in + bm25_out) / 2, 2)
        print(f"BM25 空隙存在，取 {bm25_limit}（空隙 {bm25_out:.3f}—{bm25_in:.3f}）")
    else:
        bm25_limit = None
        print("BM25 没有空隙：范围内最低值低于越界最高值，**不采用 BM25 门槛**")
        adopted = False

    payload = {
        "adopted": adopted,
        "similarity": similarity,
        "bm25": bm25_limit,
        "calibrated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "cases": len(rows),
        "out_max_similarity": round(high_out, 4),
        "in_min_similarity": round(low_in, 4),
        "out_max_bm25": round(bm25_out, 3),
        "in_min_bm25": round(bm25_in, 3),
        "note": "在 tests/cases/gate_cases.json 上测量；同源案例、无留出集。"
        + ("门槛落在空隙里。" if adopted else "范围内与越界重叠，未采用数值门槛，继续用关键词黑名单与必填对象追问。"),
    }
    if not args.report:
        (PATCH_DATA / "gate-calibration.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        DOCS = PATCH_DATA.parents[1] / "docs"
        DOCS.mkdir(exist_ok=True)
        (DOCS / "门槛校准.md").write_text(
            "\n".join(
                [
                    "# 范围门槛校准",
                    "",
                    f"测量时间：{payload['calibrated_at']}　案例：{len(rows)} 条（范围内 {len(inside)} / 越界 {len(outside)}）",
                    "",
                    f"- 范围内最低相似度：**{low_in:.4f}**；越界最高相似度：**{high_out:.4f}**",
                    f"- 范围内最低 BM25：**{bm25_in:.3f}**；越界最高 BM25：**{bm25_out:.3f}**",
                    "",
                    f"**结论：{'采用门槛 ' + str(similarity) if adopted else '不采用数值门槛'}**。",
                    "",
                    "原因：越界问题（如「明天要下雨吗」）与领域问题（如「改动细节是什么」）在这份语料上的"
                    "相似度几乎相同（0.55 上下），任何阈值都会同时误杀与放行。",
                    "当前实现因此保留两条确定性防线：关键词黑名单直接拒答，以及「没认出对象」时先追问名称；",
                    "阈值只作为 `/api/health` 的展示值，不参与回答决策。",
                    "",
                    "## 全部测量",
                    "",
                    "| 类型 | 相似度 | BM25 | 问题 |",
                    "|---|---|---|---|",
                    *[
                        f"| {row['kind']} | {row['similarity']:.4f} | {row['bm25']:.3f} | {row['question']} |"
                        for row in sorted(rows, key=lambda item: item["similarity"])
                    ],
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\n已写入 {PATCH_DATA / 'gate-calibration.json'} 与 docs/门槛校准.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
