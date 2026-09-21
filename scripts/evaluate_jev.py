"""Per-case accounting of what Jev actually changed.

For every single-target case with a known answer, compare the retrieval order with the
Jev order and classify the outcome: order kept / reordered correctly / reordered wrongly.
Writes docs/Jev效果.md + docs/Jev效果.json.

Usage:
    python scripts/evaluate_jev.py            # all blind + pinned cases
    python scripts/evaluate_jev.py --limit 40
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from config import DOCS, TESTS  # noqa: E402
from evaluate_patch import ASKED_VERSION_RE, expected_rows, expected_value, resolve_target  # noqa: E402
from patch_engine import PatchEngine, PatchRetriever  # noqa: E402
import jev  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASE_FILES = ("blind_cases.json", "blind_cases_2.json", "eval_cases.json")


def targets_for(retriever: PatchRetriever, case: dict) -> set[str]:
    if case["kind"] == "pinned":
        row = resolve_target(retriever, case["target"])
        return {row["id"]} if row else set()
    if case["kind"] == "single":
        value = expected_value(case)
        if not value:
            return set()
        rows = expected_rows(retriever, case.get("expect") or {}, value, strict_field=True)
        if not rows:
            rows = expected_rows(retriever, case.get("expect") or {}, value, strict_field=False)
        return {row["id"] for row in rows}
    return set()


def ranker_section(retriever: PatchRetriever, cases: list[dict]) -> list[str]:
    """Ranker-only comparison on the full per-subject candidate set.

    The production pipeline narrows candidates with a metadata filter before ranking,
    which leaves Jev almost nothing to do. This section removes that narrowing on
    purpose to measure the ranking ability itself: same candidates, two orderings.
    """
    stats = {"total": 0, "bm25_ok": 0, "jev_ok": 0, "both": 0, "jev_only": 0, "bm25_only": 0, "neither": 0}
    details = []
    for index, case in enumerate(cases):
        expect = case.get("expect") or {}
        patch, subject = expect.get("patch"), expect.get("subject")
        if not patch or not subject:
            continue
        targets = targets_for(retriever, case)
        if not targets:
            continue
        hits = retriever.search(case["question"], mode="bm25", k=len(retriever.chunks),
                                where={"patches": [patch], "subject": subject})
        hits = [row for row in hits if row.get("field_key") != "narrative"]
        if len(hits) < 2:
            continue
        stats["total"] += 1
        bm25_top = hits[0]["id"]
        result = jev.rerank(case["question"], hits[: jev.MAX_CANDIDATES])
        jev_top = result["ordered"][0]["id"] if result else None
        bm25_ok, jev_ok = bm25_top in targets, jev_top in targets
        stats["bm25_ok"] += int(bm25_ok)
        stats["jev_ok"] += int(jev_ok)
        stats["both"] += int(bm25_ok and jev_ok)
        stats["jev_only"] += int(jev_ok and not bm25_ok)
        stats["bm25_only"] += int(bm25_ok and not jev_ok)
        stats["neither"] += int(not bm25_ok and not jev_ok)
        details.append({
            "id": case["id"], "question": case["question"], "candidates": len(hits),
            "bm25_top": bm25_top, "jev_top": jev_top, "bm25_ok": bm25_ok, "jev_ok": jev_ok,
            "decisive": result.get("decisive") if result else None, "gap": result.get("gap") if result else None,
        })
        print(f"  [ranker] {case['id']} 候选 {len(hits)} 条 → BM25 {'对' if bm25_ok else '错'} / Jev {'对' if jev_ok else '错'}")

    lines = [
        "## 排序能力对照（去掉字段预过滤）",
        "",
        f"候选集 = 该对象在该版本的全部改动行（≥2 条才计入），共 **{stats['total']}** 条案例。",
        "",
        "| 口径 | 首选命中 |",
        "|---|---|",
        f"| BM25（元数据过滤后） | **{stats['bm25_ok']}/{stats['total']}** |",
        f"| Jev 重排（同一批候选） | **{stats['jev_ok']}/{stats['total']}** |",
        "",
        f"- 两者都对 {stats['both']}；只有 Jev 对 {stats['jev_only']}；只有 BM25 对 {stats['bm25_only']}；都错 {stats['neither']}。",
        "- 生产链路里字段预过滤已经把候选压到 1—6 条，这项能力多数时候用不上；它是候选变大或问法用词与标签不一致时的兜底。",
        "",
    ]
    (DOCS / "Jev效果-排序对照.json").write_text(
        json.dumps({"stats": stats, "rows": details}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[ranker] BM25 {stats['bm25_ok']}/{stats['total']} vs Jev {stats['jev_ok']}/{stats['total']}"
          f"（Jev 独对 {stats['jev_only']}，BM25 独对 {stats['bm25_only']}）")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Jev 决策逐条记账")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--ranker",
        action="store_true",
        help="额外做一轮排序能力对照：候选集取该对象在该版本的全部改动行（不加字段预过滤），比较 BM25 与 Jev 的首选",
    )
    args = parser.parse_args()

    cases = []
    for name in CASE_FILES:
        payload = json.loads((TESTS / "cases" / name).read_text(encoding="utf-8"))
        cases += payload.get("cases") or []
    cases = [case for case in cases if case["kind"] in ("single", "pinned")]
    if args.limit:
        cases = cases[: args.limit]

    retriever = PatchRetriever()
    retriever.build()
    engine = PatchEngine(retriever, use_jev=True, retrieval_mode="hybrid", self_check=False)
    if not engine.use_jev:
        print("没有 TYPESAFE_API_KEY，无法统计 Jev 行为。")
        return 1

    stats = {
        "total": 0, "candidates_lt2": 0,
        "kept": 0, "kept_ok": 0, "kept_bad": 0,
        "reordered": 0, "reordered_ok": 0, "reordered_bad": 0,
        "reordered_both_bad": 0,
        "no_target": 0,
    }
    rows = []
    started = time.perf_counter()
    for index, case in enumerate(cases):
        selected = None
        if not ASKED_VERSION_RE.search(case["question"]):
            selected = (case.get("expect") or {}).get("patch") or None
        session = engine.chat(engine.new(f"jev-{index}"), case["question"], selected_patch=selected)
        meta = session.get("jev")
        targets = targets_for(retriever, case)
        if not targets:
            stats["no_target"] += 1
            continue
        stats["total"] += 1
        evidence = session.get("evidence") or []
        if not meta:
            # fewer than two candidates: nothing to reorder
            stats["candidates_lt2"] += 1
            top_ok = bool(evidence) and evidence[0]["id"] in targets
            stats["kept"] += 1
            stats["kept_ok"] += int(top_ok)
            stats["kept_bad"] += int(not top_ok)
            rows.append({"id": case["id"], "question": case["question"], "action": "候选不足，未调用",
                         "retrieval_top": evidence[0]["id"] if evidence else None, "after_top": None, "ok": top_ok})
            continue
        before, after = meta.get("retrieval_top"), meta.get("after_top")
        before_ok, after_ok = before in targets, after in targets
        reordered = before != after
        stats["reordered" if reordered else "kept"] += 1
        if reordered:
            stats["reordered_ok"] += int(after_ok)
            stats["reordered_bad"] += int(not after_ok)
            stats["reordered_both_bad"] += int(not before_ok and not after_ok)
        else:
            stats["kept_ok"] += int(after_ok)
            stats["kept_bad"] += int(not after_ok)
        rows.append({
            "id": case["id"], "question": case["question"],
            "action": "改序" if reordered else ("保留（分差不足）" if not meta.get("decisive", True) else "保留（同序）"),
            "gap": meta.get("gap"), "retrieval_top": before, "after_top": after,
            "ok": after_ok, "retrieval_ok": before_ok,
        })

    elapsed = time.perf_counter() - started
    lines = [
        "# Jev 效果逐条记账",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　案例：{stats['total']} 条（单点 + 定点，来自两批盲测与同源案例）",
        "",
        "> 判据：答案行在语料中的 id。`retrieval_top` 是元数据过滤 + BM25/向量/RRF 之后的第 1 条，",
        "> `after_top` 是 Jev 重排后的第 1 条。命中判据与评测脚本一致（含数值匹配）。",
        "",
        "## 结论",
        "",
        f"- 可判定案例 **{stats['total']}** 条，其中 **{stats['candidates_lt2']}** 条过滤后候选不足 2 条，Jev 根本没有介入余地。",
        f"- Jev **改序 {stats['reordered']}** 条：改对 **{stats['reordered_ok']}**、改错 **{stats['reordered_bad']}**"
        f"（其中本来也错的 {stats['reordered_both_bad']} 条）。",
        f"- Jev **保留顺序 {stats['kept']}** 条：其中第 1 条本来就对 **{stats['kept_ok']}**、本来就错 **{stats['kept_bad']}**。",
        f"- 检索第 1 条命中 **{stats['kept_ok'] + stats['reordered_ok']}/{stats['total']}**，"
        f"Jev 之后命中 **{stats['kept_ok'] + stats['reordered_ok']}/{stats['total']}**"
        f"（差额 {stats['reordered_ok'] - stats['reordered_bad']:+d}）。",
        "",
        "## 逐条明细",
        "",
        "| 案例 | 动作 | 分差 | 检索第1 | Jev 第1 | 结果 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        result = "对" if row["ok"] else "错"
        if row.get("retrieval_ok") is not None and not row["ok"] and row["retrieval_ok"]:
            result = "改错（本来对）"
        lines.append(
            f"| {row['id']} | {row['action']} | {row.get('gap', '—')} | {row['retrieval_top'] or '—'} | {row['after_top'] or '—'} | {result} |"
        )
    lines += ["", f"耗时 {elapsed:.0f}s。", ""]

    if args.ranker:
        lines += ranker_section(retriever, cases)

    DOCS.mkdir(exist_ok=True)
    (DOCS / "Jev效果.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (DOCS / "Jev效果.json").write_text(
        json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "stats": stats, "rows": rows},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"可判定 {stats['total']} 条：改序 {stats['reordered']}（改对 {stats['reordered_ok']} / 改错 {stats['reordered_bad']}）"
          f"｜保留 {stats['kept']}（本来对 {stats['kept_ok']} / 本来错 {stats['kept_bad']}）｜候选不足 {stats['candidates_lt2']}")
    print(f"报告：{DOCS / 'Jev效果.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
