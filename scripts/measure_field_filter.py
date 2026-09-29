"""Measure: does the hard field_key filter help or hurt across the effect set?

For every case, compare the candidate set the reranker receives with the filter (current
behaviour) against the same scope without it, and report where the correct row lands.
This is the evidence for keeping or removing the filter, not an opinion.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import patch_engine  # noqa: E402
from evaluate_routing import resolve_row  # noqa: E402

CASES = ROOT / "tests" / "cases" / "routing_effect_cases.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval", default="hybrid", choices=["bm25", "hybrid"])
    args = parser.parse_args()

    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    retriever = patch_engine.PatchRetriever()
    if args.retrieval != "bm25":
        retriever.build()

    tally = {"filter_helps": 0, "filter_hurts": 0, "same": 0, "filter_absent": 0}
    rows_out = []
    for case in cases:
        spec = case["expect"]["rows"]
        target = resolve_row(retriever, spec)
        plan = patch_engine.PatchEngine(retriever).plan(case["question"])
        keys = (plan.get("field_keys") or {}).value or []
        base = {"patches": [spec["patch"]], "subject": spec["subject"]}
        if spec["ability"] != "base":
            base["ability"] = spec["ability"]

        def rank(where):
            hits = retriever.search(case["question"], mode=args.retrieval,
                                    k=len(retriever.chunks), where=where)
            return (next((i for i, row in enumerate(hits, 1) if row["id"] == target), None),
                    len(hits))

        with_filter, count_filtered = rank({**base, "field_keys": keys}) if keys else (None, 0)
        without, count_plain = rank(base)
        record = {
            "id": case["id"], "keys": keys, "ability": spec["ability"],
            "target_field_key": spec["field_key"],
            "rank_with_filter": with_filter, "candidates_with_filter": count_filtered,
            "rank_without_filter": without, "candidates_without_filter": count_plain,
            "question": case["question"],
        }
        rows_out.append(record)
        if not keys:
            tally["same"] += 1
            continue
        if with_filter is None and without is not None:
            tally["filter_absent"] += 1
        elif with_filter is None and without is None:
            tally["same"] += 1
        elif without is None:
            tally["filter_helps"] += 1
        elif with_filter is None:
            tally["filter_hurts"] += 1
        elif with_filter < without:
            tally["filter_helps"] += 1
        elif with_filter > without:
            tally["filter_hurts"] += 1
        else:
            tally["same"] += 1

    print(f"检索口径 {args.retrieval}")
    print("  字段过滤让正确行名次更好 :", tally["filter_helps"])
    print("  字段过滤让正确行名次更差 :", tally["filter_hurts"])
    print("  结果相同                 :", tally["same"])
    print("  过滤后正确行直接消失     :", tally["filter_absent"])
    print()
    print("受影响（名次变化或消失）的案例：")
    for row in rows_out:
        changed = (row["rank_with_filter"] != row["rank_without_filter"])
        if not changed or not row["keys"]:
            continue
        print(f"  {row['id']:<5} {str(row['keys']):<28} 期望字段 {row['target_field_key']:<14} "
              f"带过滤 {row['rank_with_filter']}/{row['candidates_with_filter']}"
              f"  不过滤 {row['rank_without_filter']}/{row['candidates_without_filter']}"
              f"  | {row['question'][:38]}")
    Path("docs/字段过滤对照.json").write_text(
        json.dumps({"retrieval": args.retrieval, "tally": tally, "rows": rows_out},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n数据：docs/字段过滤对照.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
