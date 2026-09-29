"""Attribute the cases that fail in both scopes: wrong skill, or right skill / wrong row.

The distinction decides what the result means. If the router picks the correct skill and the
answer is still wrong, the residual error belongs to reranking and no amount of routing
improvement would fix it; only the cases where the *skill itself* was misread are routing
failures.

Run with no arguments it analyses **every case that neither scope solves**, which is the set
worth explaining, and writes `docs/Jev分层路由-失败归因.md` so the breakdown is reproducible
rather than something a human assembled by hand once.

Usage:
    python scripts/attribute_routing_errors.py
    python scripts/attribute_routing_errors.py --report docs/Jev分层路由-效果集.json
    python scripts/attribute_routing_errors.py --ids e02,e06
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import patch_engine  # noqa: E402
import routing  # noqa: E402

ROUTING_CASES = ROOT / "tests" / "cases" / "routing_effect_cases.json"
DEFAULT_REPORT = ROOT / "docs" / "Jev分层路由-效果集.json"
OUT_MD = ROOT / "docs" / "Jev分层路由-失败归因.md"

KIND_NOTE = {
    "技能对、行选错": "路由已把正确技能收窄进来，但重排没选中那条行 —— 属于**重排**的残余错误",
    "技能认错": "模型把技能判断错了，且该技能不在候选路径里 —— 属于**路由**的错误",
    "无有效信号": "模型没给出达到门槛的判断，链路退化为不过滤 —— 既非明确对也非明确错",
}


def unsolved_case_ids(report_path: Path) -> list[str]:
    """Cases that neither off nor active solves in any round."""
    if not report_path.exists():
        return []
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or {}
    solved: dict[str, dict[str, list[bool]]] = {}
    for scope in ("off", "active"):
        for row in rows.get(scope, []):
            solved.setdefault(row["id"], {}).setdefault(scope, []).append(bool(row["hit@1"]))
    return sorted(case_id for case_id, marks in solved.items()
                  if not any(marks.get("off", [])) and not any(marks.get("active", [])))


def attribute(case: dict, engine) -> tuple[str, dict]:
    expected = case["expect"]["rows"]["ability"]
    session = engine.chat(engine.new("attr-" + case["id"]), case["question"])
    routing_payload = session["routing"]
    paths = [path["label"] for path in (routing_payload.get("paths") or [])]
    slot = session["query_plan"]["slots"].get("ability") or {}
    chosen, confidence = slot.get("value"), slot.get("confidence")
    covered = any(expected in label for label in paths)

    if covered:
        kind = "技能对、行选错"
    elif chosen is None or (confidence or 0) < routing.RoutingConfig().min_fallback_confidence:
        kind = "无有效信号"
    else:
        kind = "技能认错"
    top = (session.get("evidence") or [{}])[0]
    return kind, {
        "expected_ability": expected,
        "chosen_ability": chosen,
        "confidence": confidence,
        "branches": paths,
        "beam": bool(routing_payload.get("beam_used")),
        "top_ability": top.get("ability") or "base",
        "top_field_key": top.get("field_key"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(ROUTING_CASES))
    parser.add_argument("--ids", default="", help="逗号分隔，只分析这些案例（默认：两种口径都答不对的全部）")
    parser.add_argument("--report", default=str(DEFAULT_REPORT),
                        help="效果集报告 JSON，用来挑出「两种口径都答不对」的案例")
    parser.add_argument("--repeat", type=int, default=3,
                        help="重复 N 次，报告归因的分布（模型有随机性，单次快照会误导）")
    parser.add_argument("--out", default=str(OUT_MD), help="归因报告输出路径（留空则不写文件）")
    args = parser.parse_args()

    payload = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = payload["cases"]
    if args.ids:
        wanted = {item.strip() for item in args.ids.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in wanted]
        selection = "--ids 指定"
    else:
        unsolved = unsolved_case_ids(Path(args.report))
        if unsolved:
            cases = [case for case in cases if case["id"] in set(unsolved)]
            selection = f"取自 {Path(args.report).name}：两种口径都答不对的案例"
        else:
            selection = "全部案例（未找到效果集报告）"
    print(f"分析对象：{len(cases)} 条（{selection}）\n")

    retriever = patch_engine.PatchRetriever()
    engine = patch_engine.PatchEngine(retriever, use_jev=False, self_check=False,
                                      retrieval_mode="bm25", routing_mode=routing.MODE_ACTIVE)

    buckets: dict[str, list[str]] = collections.defaultdict(list)
    detail: dict[str, dict] = {}
    rounds = max(args.repeat, 1)
    # Per-round tallies plus a per-case majority, so one unlucky sample cannot pass for the
    # verdict: the model re-decides each round, and the split between "misread the skill" and
    # "picked the wrong row" does move between rounds.
    per_round: list[dict[str, int]] = []
    case_kinds: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    last_info: dict[str, dict] = {}
    for round_index in range(rounds):
        tally: dict[str, int] = collections.Counter()
        if rounds > 1:
            print(f"[round {round_index + 1}/{rounds}]")
        for case in cases:
            kind, info = attribute(case, engine)
            tally[kind] += 1
            case_kinds[case["id"]][kind] += 1
            last_info[case["id"]] = info
            if round_index == 0:
                detail[case["id"]] = info
                print(f"  {case['id']:<5} 期望={info['expected_ability']:<5} "
                      f"模型={str(info['chosen_ability']):<5} conf={info['confidence']} "
                      f"首选={info['top_ability']}/{info['top_field_key']} "
                      f"分支={info['branches']}")
        per_round.append(dict(tally))

    # Majority verdict per case; ties resolved toward the more conservative reading.
    for case_id, counter in case_kinds.items():
        kind = counter.most_common(1)[0][0]
        buckets[kind].append(case_id)
    for kind in buckets:
        buckets[kind].sort()

    print()
    means = {kind: sum(round_tally.get(kind, 0) for round_tally in per_round) / rounds
             for kind in KIND_NOTE}
    for kind in KIND_NOTE:
        if means[kind] or buckets.get(kind):
            spread = "、".join(str(round_tally.get(kind, 0)) for round_tally in per_round)
            print(f"{kind:<12} 均值 {means[kind]:.1f}　各轮 [{spread}]　案例 {buckets.get(kind, [])}")
    right_skill = means["技能对、行选错"]
    wrong = means["技能认错"]
    print()
    if right_skill:
        print(f"→ 平均 {right_skill:.1f} 条的技能已被正确收窄，残余错误属于**重排**，路由再准也修不了。")
    if wrong:
        print(f"→ 平均 {wrong:.1f} 条是真的技能认错，属于路由本身的错误。")

    if args.out:
        out_path = Path(args.out)
        lines = [
            "# 分层路由失败归因",
            "",
            f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　"
            f"分析对象：{len(cases)} 条（{selection}）　检索口径：BM25　重复：{rounds} 次",
            "",
            "> 目的：把「两种口径都答不对」的案例分开 —— 是**路由把技能认错了**，",
            "> 还是**技能认对了但重排选错行**。这两者的责任方不同，修法也不同。",
            "",
            "## 结论（重复 %d 次后的均值）" % rounds,
            "",
            "| 归因 | 均值 | 各轮 | 责任方 |",
            "|---|---|---|---|",
        ]
        for kind in ("技能对、行选错", "技能认错", "无有效信号"):
            if means[kind] or buckets.get(kind):
                owner = ("重排" if kind == "技能对、行选错"
                         else "路由" if kind == "技能认错" else "链路（保守退化）")
                spread = "、".join(str(round_tally.get(kind, 0)) for round_tally in per_round)
                lines.append(f"| {kind} | {means[kind]:.1f} | {spread} | {owner} |")
        lines += [
            "",
            "模型每轮重新判断，所以归因会在轮次间小幅变动；这里报的是均值与各轮分布，"
            "不是一次快照。逐条明细取第 1 轮的判定。",
            "",
            "说明：",
        ]
        for kind, note in KIND_NOTE.items():
            if means[kind] or buckets.get(kind):
                lines.append(f"- **{kind}**（均值 {means[kind]:.1f}）：{note}。")
        lines += [
            "",
            "## 逐条明细",
            "",
            "| 案例 | 问题 | 期望技能 | 模型判断 | 置信度 | 实际首选 | 分支 | 归因 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for case in cases:
            info = detail[case["id"]]
            kind = next((k for k, ids in buckets.items() if case["id"] in ids), "—")
            counter = case_kinds[case["id"]]
            varied = "" if len(counter) == 1 else f"（{rounds} 轮中出现过 {'、'.join(counter)}）"
            lines.append(
                f"| {case['id']} | {case['question']} | {info['expected_ability']} | "
                f"{info['chosen_ability'] if info['chosen_ability'] is not None else '（未定）'} | "
                f"{info['confidence'] if info['confidence'] is not None else '—'} | "
                f"{info['top_ability']}/{info['top_field_key']} | "
                f"{'、'.join(info['branches'])} | {kind}{varied} |"
            )
        lines += [
            "",
            "## 怎么用这张表",
            "",
            f"- 「技能对、行选错」占多数（均值 {means['技能对、行选错']:.1f}/{len(cases)}）时，"
            "下一步该优化**重排**，而不是继续调路由阈值。",
            "- 想让路由本身变好，只看「技能认错」那几行，并把它们当作路由的失败率"
            f"（均值 {means['技能认错']:.1f}/{len(cases)}）。",
            "- 「无有效信号」说明模型没给出达门槛的判断，链路按保守方式退化；"
            "这时既不该记成路由的对，也不该记成它的错。",
            "- 明细列里带「N 轮中出现过…」的案例说明模型每轮判断不一致，"
            "这类案例不适合用来论证任何单点结论。",
        ]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out_path.with_suffix(".json")).write_text(
            json.dumps({
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "analysed": len(cases),
                "selection": selection,
                "rounds": rounds,
                "per_round": per_round,
                "means": {kind: round(value, 2) for kind, value in means.items()},
                "buckets": {k: v for k, v in buckets.items()},
                "right_skill_wrong_row": means["技能对、行选错"],
                "wrong_skill": means["技能认错"],
                "no_signal": means["无有效信号"],
                "detail": last_info,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n报告：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
