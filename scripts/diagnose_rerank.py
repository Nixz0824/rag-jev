"""Reranking diagnostic: for each failing case, show the exact decision the engine made.

The question "right skill, wrong row" has three very different causes and they need different
fixes, so this separates them:

* **absent**   — the correct row never reached the reranker (a filter dropped it);
* **below**    — the correct row was in the candidate list but ranked below the chosen row;
* **tie-label** — both rows are in the list with near-identical labels, so no ranking signal
                 distinguishes them and the question itself is the weak part.

Prints one block per case plus a tally, and writes the JSON so a fix can be measured against it.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import patch_engine  # noqa: E402
import routing  # noqa: E402
from evaluate_routing import resolve_row  # noqa: E402

CASES = ROOT / "tests" / "cases" / "routing_effect_cases.json"
EFFECT_REPORT = ROOT / "docs" / "Jev分层路由-效果集-hybrid.json"
OUT = ROOT / "docs" / "重排诊断.md"


def failed_ids(report_path: Path) -> list[str]:
    if not report_path.exists():
        return []
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    solved: dict[str, dict[str, list[bool]]] = {}
    for scope in ("off", "active"):
        for row in (payload.get("rows") or {}).get(scope, []):
            solved.setdefault(row["id"], {}).setdefault(scope, []).append(bool(row["hit@1"]))
    return sorted(cid for cid, marks in solved.items()
                  if not any(marks.get("off", [])) and not any(marks.get("active", [])))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(CASES))
    parser.add_argument("--report", default=str(EFFECT_REPORT))
    parser.add_argument("--ids", default="", help="逗号分隔；默认取效果集里两种口径都答不对的案例")
    parser.add_argument("--retrieval", default="hybrid", choices=["bm25", "hybrid"])
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    cases = {case["id"]: case for case in
             json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]}
    ids = ([item.strip() for item in args.ids.split(",") if item.strip()]
           if args.ids else failed_ids(Path(args.report)))
    retriever = patch_engine.PatchRetriever()
    if args.retrieval != "bm25":
        retriever.build()
    engine = patch_engine.PatchEngine(retriever, use_jev=False, self_check=False,
                                      retrieval_mode=args.retrieval,
                                      routing_mode=routing.MODE_ACTIVE)

    verdicts: dict[str, list[str]] = collections.defaultdict(list)
    detail: dict[str, dict] = {}
    for case_id in ids:
        case = cases.get(case_id)
        if not case:
            continue
        spec = case["expect"]["rows"]
        target = resolve_row(retriever, spec)
        session = engine.chat(engine.new("diag-" + case_id), case["question"])
        evidence = session.get("evidence") or []
        chosen = evidence[0] if evidence else {}
        rank = next((i for i, row in enumerate(evidence, 1) if row["id"] == target), None)
        if rank is None:
            verdict = "absent"
        elif rank == 1:
            verdict = "ok"
        elif chosen.get("field_key") == (next((r.get("field_key") for r in evidence
                                              if r["id"] == target), None)):
            verdict = "tie-label"
        else:
            verdict = "below"
        verdicts[verdict].append(case_id)
        detail[case_id] = {
            "question": case["question"],
            "verdict": verdict,
            "target": {"id": target, "ability": spec["ability"], "field_key": spec["field_key"],
                       "value": spec["value"]},
            "chosen": {"ability": chosen.get("ability"), "field_key": chosen.get("field_key"),
                       "field": chosen.get("field"), "new_value": chosen.get("new_value")},
            "target_rank_in_evidence": rank,
            "evidence": [{"ability": row.get("ability"), "field_key": row.get("field_key"),
                          "field": row.get("field"), "new_value": row.get("new_value"),
                          "is_target": row["id"] == target} for row in evidence],
            "beam": bool((session.get("routing") or {}).get("beam_used")),
            "branches": [p["label"] for p in ((session.get("routing") or {}).get("paths") or [])],
        }
        print(f"\n=== {case_id} [{verdict}] {case['question']}")
        print(f"    正确：{spec['ability']}/{spec['field_key']} {spec['value'][:32]!r}"
              f"  在证据里排 {rank}")
        print(f"    选中：{chosen.get('ability')}/{chosen.get('field_key')} "
              f"{chosen.get('field')!r}")
        for index, row in enumerate(evidence[:5], 1):
            mark = "  <-- 正确" if row["id"] == target else ""
            print(f"      {index}. {row.get('ability')}/{row.get('field_key')} "
                  f"{row.get('field')!r} = {(row.get('new_value') or '')[:26]}{mark}")

    print("\n=== 归因汇总 ===")
    for verdict, case_ids in sorted(verdicts.items(), key=lambda kv: -len(kv[1])):
        print(f"  {verdict:<10} {len(case_ids):>2} 条  {case_ids}")

    if args.out:
        out_path = Path(args.out)
        lines = [
            "# 重排失败诊断",
            "",
            f"检索口径：{args.retrieval}　案例：{len(detail)} 条",
            "",
            "> 「技能认对了、行选错了」有三种完全不同的原因，修法也不同：",
            "> `absent` 是候选被过滤掉了、`below` 是候选在但排后面、`tie-label` 是两条行标签太像。",
            "",
            "## 归因汇总",
            "",
            "| 归因 | 条数 | 案例 |",
            "|---|---|---|",
        ]
        for verdict, case_ids in sorted(verdicts.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"| {verdict} | {len(case_ids)} | {'、'.join(case_ids)} |")
        lines += ["", "## 逐条明细", ""]
        for case_id, info in detail.items():
            lines += [
                f"### {case_id} [{info['verdict']}] {info['question']}",
                "",
                f"- 正确行：`{info['target']['ability']}/{info['target']['field_key']}` "
                f"`{info['target']['value']}`，在证据里排 "
                f"{info['target_rank_in_evidence'] or '不在前 5'}",
                f"- 引擎选中：`{info['chosen']['ability']}/{info['chosen']['field_key']}` "
                f"{info['chosen']['field']!r} = `{(info['chosen']['new_value'] or '')[:40]}`",
                f"- 分支：{info['branches']}（分叉：{'是' if info['beam'] else '否'}）",
                "",
                "| 证据顺序 | 技能/字段键 | 字段标签 | 新值 | 是否目标 |",
                "|---|---|---|---|---|",
            ]
            for index, row in enumerate(info["evidence"], 1):
                lines.append(f"| {index} | {row['ability']}/{row['field_key']} | "
                             f"{row['field']} | {(row['new_value'] or '')[:40]} | "
                             f"{'是' if row['is_target'] else ''} |")
            lines.append("")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out_path.with_suffix(".json")).write_text(
            json.dumps({"retrieval": args.retrieval,
                        "verdicts": {k: v for k, v in verdicts.items()},
                        "detail": detail}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n报告：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
