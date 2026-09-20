"""Query-understanding evaluation on a blind set (no corpus values involved).

Expectations describe only what the parser should extract — subject, patch, ability,
field, mode, intent, guard. Nothing here is read out of the corpus, so this is the
one measurement that is not same-source.

Usage:
    python scripts/evaluate_parse.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DOCS, TESTS  # noqa: E402
from engine import BLOCKED, GREETING  # noqa: E402
from patch_engine import (  # noqa: E402
    OFF_TOPIC_INTENT,
    OTHER_SERVER_INTENT,
    POSITION_INTENT,
    UNSUPPORTED_INTENT,
    PatchEngine,
    PatchRetriever,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES = TESTS / "cases" / "parse_cases.json"
# expectation key -> key in the parsed query dict
SLOTS = {"subject": "subject", "patches": "patches", "ability": "ability", "field_any": "field_keys",
         "mode": "mode", "direction": "direction", "overview": "overview", "outside": "outside"}


def guard_of(text: str) -> str | None:
    """Mirror the order the chat pipeline checks guards in."""
    if GREETING.fullmatch(text):
        return "greeting"
    if BLOCKED.search(text):
        return "blocked"
    if UNSUPPORTED_INTENT.search(text):
        return "unsupported"
    if OTHER_SERVER_INTENT.search(text):
        return "other_server"
    if OFF_TOPIC_INTENT.search(text):
        return "off_topic"
    if POSITION_INTENT.search(text):
        return "position"
    return None


def check(expect: dict, query: dict, guard: str | None) -> list[str]:
    problems = []
    if "guard" in expect:
        if guard != expect["guard"]:
            problems.append(f"守卫 {guard or '无'} ≠ {expect['guard']}")
        return problems
    if guard:
        problems.append(f"被守卫拦截：{guard}")
        return problems
    for slot in SLOTS:
        if slot not in expect:
            continue
        wanted, got = expect[slot], query.get(SLOTS[slot])
        if slot == "field_any":
            if not set(wanted) & set(got or []):
                problems.append(f"字段 {got} 不含 {wanted}")
        elif slot == "patches":
            if list(got or []) != list(wanted):
                problems.append(f"版本 {got} ≠ {wanted}")
        elif slot == "outside":
            if not set(wanted) <= set(got or []):
                problems.append(f"越界版本 {got} 不含 {wanted}")
        elif got != wanted:
            problems.append(f"{slot} {got!r} ≠ {wanted!r}")
    return problems


def main() -> int:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    retriever = PatchRetriever()
    engine = PatchEngine(retriever, use_jev=False)

    slot_total: dict[str, int] = {slot: 0 for slot in SLOTS}
    slot_ok: dict[str, int] = {slot: 0 for slot in SLOTS}
    guard_total = guard_ok = 0
    passed = 0
    failures = []

    for case in cases:
        text = case["question"]
        guard = guard_of(text)
        query = engine.parse(text)
        problems = check(case["expect"], query, guard)
        if problems:
            failures.append((case["id"], text, problems))
        else:
            passed += 1
        if "guard" in case["expect"]:
            guard_total += 1
            guard_ok += int(not problems)
        for slot in SLOTS:
            if slot in case["expect"]:
                slot_total[slot] += 1
                slot_ok[slot] += int(not any(p.startswith(slot[:4]) for p in problems))

    print(f"查询理解：{passed}/{len(cases)} 条完全通过")
    for slot in SLOTS:
        if slot_total[slot]:
            print(f"  {slot:<9} {slot_ok[slot]}/{slot_total[slot]}")
    if guard_total:
        print(f"  守卫      {guard_ok}/{guard_total}")
    for case_id, text, problems in failures:
        print(f"  FAIL {case_id} 「{text}」 → {'；'.join(problems)}")

    lines = [
        "# 查询理解盲测",
        "",
        f"生成时间：{__import__('time').strftime('%Y-%m-%d %H:%M')}　案例：{len(cases)} 条",
        "",
        "> 期望只描述解析结果（主体/版本/技能/字段/模式/意图/守卫），不引用语料数值，"
        "因此这一组与语料不同源，也不受检索与 Jev 影响。",
        "",
        f"**完全通过 {passed}/{len(cases)}**",
        "",
        "| 槽位 | 通过 |",
        "|---|---|",
    ]
    for slot in SLOTS:
        if slot_total[slot]:
            lines.append(f"| {slot} | {slot_ok[slot]}/{slot_total[slot]} |")
    if guard_total:
        lines.append(f"| guard | {guard_ok}/{guard_total} |")
    if failures:
        lines += ["", "## 未通过", "", "| 案例 | 问题 | 解析结果 |", "|---|---|---|"]
        for case_id, text, problems in failures:
            lines.append(f"| {case_id} | {'；'.join(problems)} | {text} |")
    (DOCS / "查询理解.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告：{DOCS / '查询理解.md'}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
