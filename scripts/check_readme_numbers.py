"""Cross-check the numbers quoted in README against the generated reports.

Every figure in the prose is supposed to come from `docs/*.json`. This walks the READMEs and
the reports and flags any report metric that does not appear in the prose, plus any suspicious
claim (a "zero harm"-style absolute) that the current reports contradict.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(r"D:\python项目\RAG Jev")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DOCS = REPO / "docs"


def load(name: str) -> dict:
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def fmt(value) -> str:
    number = float(value)
    return str(int(number)) if number == int(number) else f"{number:.1f}"


def main() -> int:
    cn = (REPO / "README.md").read_text(encoding="utf-8")
    en = (REPO / "README.en.md").read_text(encoding="utf-8")
    prose = cn + "\n" + en

    problems: list[str] = []
    checked = 0

    def expect(token: str, where: str) -> None:
        nonlocal checked
        checked += 1
        if token not in prose:
            problems.append(f"{where}：期望在 README 里出现 {token!r}，但没有")

    # --- routing effect set -------------------------------------------------
    eff = load("Jev分层路由-效果集-hybrid.json")
    off = eff["arms"]["off"]
    active = eff["arms"]["active"]
    expect(f"{fmt(off['hit@1'])}/{off['cases_per_round']}", "hybrid off 命中@1")
    expect(fmt(active["hit@1"]), "hybrid active 命中@1")
    expect(f"{fmt(active['hit@5'])}/{active['cases_per_round']}", "hybrid 命中@5")

    rows_off: dict[str, list[bool]] = {}
    rows_active: dict[str, list[bool]] = {}
    for row in eff["rows"]["off"]:
        rows_off.setdefault(row["id"], []).append(bool(row["hit@1"]))
    for row in eff["rows"]["active"]:
        rows_active.setdefault(row["id"], []).append(bool(row["hit@1"]))
    only_active = sum(1 for k in rows_off
                      if not any(rows_off[k]) and any(rows_active.get(k, [])))
    only_off = sum(1 for k in rows_off
                   if any(rows_off[k]) and not any(rows_active.get(k, [])))
    expect(str(only_active), "只有 active 答对数")
    print(f"当前 hybrid 报告：off {fmt(off['hit@1'])}/{off['cases_per_round']}　"
          f"active {fmt(active['hit@1'])}/{active['cases_per_round']}　"
          f"只有 active 答对 {only_active}　只有 off 答对 {only_off}　"
          f"改善 {eff['improved']}　损害 {eff['hurt']}")

    # Multi-run bounds, if present.
    multi = DOCS / "路由效果-多次运行.json"
    if multi.exists():
        data = json.loads(multi.read_text(encoding="utf-8"))
        actives = [record["active"] for record in data["runs"]]
        hurts = [record["hurt"] for record in data["runs"]]
        expect(f"{fmt(sum(actives) / len(actives))}", "多次运行 active 均值")
        print(f"多次运行：active {actives}（均值 {sum(actives) / len(actives):.2f}）　"
              f"损害 {hurts}")

    # --- other reports -----------------------------------------------------
    ranker = load("Jev效果-排序对照.json")["stats"]
    expect(f"{ranker['jev_ok']}/{ranker['total']}", "排序对照 Jev 命中")
    expect(f"{ranker['bm25_ok']}/{ranker['total']}", "排序对照 BM25 命中")

    check = load("Jev自检.json")
    expect(f"{check['detected']}/{check['cases']}", "自检检出")
    expect(f"{check['control']['mean']:.2f}", "自检对照支持度")

    same = load("评测报告.json")["arms"]
    for arm in ("hybrid", "hybrid+jev"):
        if arm in same and same[arm].get("pinned", {}).get("total"):
            pinned = same[arm]["pinned"]
            expect(f"{pinned['hit@1']}/{pinned['total']}", f"{arm} 定点命中")
    jev_arm = same.get("hybrid+jev") or {}
    if (jev_arm.get("selfcheck") or {}).get("total"):
        sc = jev_arm["selfcheck"]
        expect(f"{sc['pass']}/{sc['total']}", "同源自检")

    blind = load("盲测报告-合并.json")["arms"]
    for arm in ("hybrid", "hybrid+jev"):
        if arm in blind:
            single = blind[arm]["single"]
            expect(f"{single['value_ok']}/{single['value_total']}", f"盲测数值正确（{arm}）")

    # --- contradictions ---------------------------------------------------
    forbidden = [
        (r"损害\s*0\s*条", "声称损害为 0（当前报告显示有损害）"),
        (r"harms? no previously-correct", "声称零损害"),
        (r"只有 off 答对\s*0\s*条", "声称只有 off 答对 0 条（应为 0～1）"),
    ]
    for pattern, reason in forbidden:
        for match in re.finditer(pattern, prose):
            line = prose[: match.start()].count("\n") + 1
            window = prose[max(0, match.start() - 120): match.start() + 120]
            if "更正" in window or "corrected" in window or "was one lucky" in window:
                continue  # an explicit correction of the old claim is fine
            problems.append(f"与报告矛盾：{reason}（偏移 {match.start()}，约第 {line} 行）")

    print(f"\n检查了 {checked} 个数字引用")
    if problems:
        print("=== 不一致 ===")
        for item in problems:
            print("  ✗", item)
        return 1
    print("=== 全部一致 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
