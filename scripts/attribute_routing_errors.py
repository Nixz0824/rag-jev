"""Attribute the cases that fail in both scopes: wrong skill, or right skill / wrong row.

The distinction decides what the result means. If the router picks the correct skill and the
answer is still wrong, the residual error belongs to reranking and no amount of routing
improvement would fix it; only the cases where the *skill itself* was misread are routing
failures.

Usage:
    python scripts/attribute_routing_errors.py --cases tests/cases/routing_effect_cases.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import patch_engine  # noqa: E402
import routing  # noqa: E402

ROUTING_CASES = ROOT / "tests" / "cases" / "routing_effect_cases.json"


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
    return kind, {
        "expected_ability": expected,
        "chosen_ability": chosen,
        "confidence": confidence,
        "branches": paths,
        "beam": bool(routing_payload.get("beam_used")),
        "evidence_top": (session.get("evidence") or [{}])[0].get("ability"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(ROUTING_CASES))
    parser.add_argument("--ids", default="", help="逗号分隔，只分析这些案例（默认：全部）")
    args = parser.parse_args()

    payload = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = payload["cases"]
    if args.ids:
        wanted = {item.strip() for item in args.ids.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in wanted]

    retriever = patch_engine.PatchRetriever()
    engine = patch_engine.PatchEngine(retriever, use_jev=False, self_check=False,
                                      retrieval_mode="bm25", routing_mode=routing.MODE_ACTIVE)

    buckets: dict[str, list[str]] = collections.defaultdict(list)
    detail: dict[str, dict] = {}
    for case in cases:
        kind, info = attribute(case, engine)
        buckets[kind].append(case["id"])
        detail[case["id"]] = info

    for case in cases:
        info = detail[case["id"]]
        print(f"  {case['id']:<5} 期望={info['expected_ability']:<5} "
              f"模型={str(info['chosen_ability']):<5} conf={info['confidence']} "
              f"分支={info['branches']}")

    print()
    for kind, ids in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"{kind:<12} {len(ids):>3} 条  {ids}")
    print()
    right_skill = len(buckets.get("技能对、行选错", []))
    if right_skill:
        print(f"→ {right_skill} 条的技能已被正确收窄，残余错误属于**重排**，路由再准也修不了。")
    wrong = len(buckets.get("技能认错", []))
    if wrong:
        print(f"→ {wrong} 条是真的技能认错，属于路由本身的错误。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
