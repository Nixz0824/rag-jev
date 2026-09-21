"""Does the answer self-check actually catch wrong numbers?

Takes answered cases, keeps a control copy, and makes a mutated copy whose last number
is changed by one. Both are sent to the same Jev self-check that the product uses.
A useful guard has to flag the mutated copy and stay quiet on the control.

Writes docs/Jev自检.md + docs/Jev自检.json.

Usage:
    python scripts/evaluate_selfcheck.py --limit 24
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from config import DOCS, TESTS  # noqa: E402
from evaluate_patch import ASKED_VERSION_RE  # noqa: E402
from patch_engine import PatchEngine, PatchRetriever  # noqa: E402
import jev  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASE_FILES = ("blind_cases.json", "blind_cases_2.json", "eval_cases.json")
THRESHOLD = 0.5
NUMBER_RE = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?")


def mutate(statement: str) -> str | None:
    """Change the last number in a rendered line by one, keeping its formatting."""
    matches = list(NUMBER_RE.finditer(statement))
    if not matches:
        return None
    last = matches[-1]
    value = float(last.group(0))
    replacement = f"{value + 1:g}" if value >= 0 else f"{value - 1:g}"
    return statement[: last.start()] + replacement + statement[last.end() :]


def judge_in_batches(pairs: list[tuple[str, str]], batch: int = 12) -> dict | None:
    """jev.judge_many caps a call at 12 pairs, so split and merge."""
    scores: list[float] = []
    cost = 0.0
    for start in range(0, len(pairs), batch):
        chunk = pairs[start : start + batch]
        result = jev.judge_many(chunk)
        if not result:
            return None
        scores += result["scores"]
        cost += result["cost_usd"]
    if not scores:
        return None
    return {
        "scores": scores,
        "min": min(scores),
        "mean": round(sum(scores) / len(scores), 4),
        "cost_usd": round(cost, 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="回答自检的注入检出率")
    parser.add_argument("--limit", type=int, default=24, help="案例数（每 12 条两次调用）")
    args = parser.parse_args()

    cases = []
    for name in CASE_FILES:
        payload = json.loads((TESTS / "cases" / name).read_text(encoding="utf-8"))
        cases += [case for case in payload.get("cases") or [] if case["kind"] in ("single", "pinned")]

    retriever = PatchRetriever()
    retriever.build()
    engine = PatchEngine(retriever, use_jev=False, retrieval_mode="hybrid")
    if not jev.available():
        print("没有 TYPESAFE_API_KEY，无法测量自检。")
        return 1

    samples = []
    for index, case in enumerate(cases):
        if len(samples) >= args.limit:
            break
        selected = None
        if not ASKED_VERSION_RE.search(case["question"]):
            selected = (case.get("expect") or {}).get("patch") or None
        session = engine.chat(engine.new(f"selfcheck-{index}"), case["question"], selected_patch=selected)
        evidence = [row for row in (session.get("evidence") or []) if row.get("field_key") != "narrative"]
        if not evidence:
            continue
        row = evidence[0]
        statement = PatchEngine._render(row, with_patch=False)
        mutated = mutate(statement)
        if not mutated or mutated == statement:
            continue
        samples.append({
            "id": case["id"],
            "question": case["question"],
            "statement": statement,
            "mutated": mutated,
            "evidence": PatchEngine._evidence_for(row),
        })

    controls = judge_in_batches([(s["statement"], s["evidence"]) for s in samples])
    mutations = judge_in_batches([(s["mutated"], s["evidence"]) for s in samples])
    if not controls or not mutations:
        print("Jev 调用失败。")
        return 1

    flagged = [score < THRESHOLD for score in mutations["scores"]]
    false_alarms = [score < THRESHOLD for score in controls["scores"]]
    detected = sum(flagged)
    quiet = len(false_alarms) - sum(false_alarms)
    cost = controls["cost_usd"] + mutations["cost_usd"]

    lines = [
        "# 回答自检的注入检出率",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　案例：{len(samples)} 条　阈值：支持度 < {THRESHOLD} 视为不合格",
        "",
        "> 做法：把已经答对的回答原样送进自检（对照组），再把渲染行里最后一个数字改掉 1（注入组），",
        "> 两组用同一个 `jev.judge_many` 判定。判据与产品一致：支持度 < 0.5 会在回答里加提示。",
        "",
        "## 结论",
        "",
        f"- **注入检出：{detected}/{len(samples)}**（把数字改错后，自检判定为不合格）",
        f"- **对照误报：{sum(false_alarms)}/{len(samples)}**（正确回答被误判为不合格）",
        f"- 对照组支持度：最低 {controls['min']:.2f}、平均 {controls['mean']:.2f}；"
        f"注入组支持度：最低 {mutations['min']:.2f}、平均 {mutations['mean']:.2f}",
        f"- 两次调用合计 ${cost:.5f}（{len(samples)} 条对照 + {len(samples)} 条注入，均摊 "
        f"${cost / (2 * len(samples)):.6f}/条）",
        "",
        "## 逐条明细",
        "",
        "| 案例 | 原句 | 注入后 | 对照支持度 | 注入支持度 | 是否检出 |",
        "|---|---|---|---|---|---|",
    ]
    for index, sample in enumerate(samples):
        lines.append(
            f"| {sample['id']} | {sample['statement'][:44]} | {sample['mutated'][-28:]} |"
            f" {controls['scores'][index]:.2f} | {mutations['scores'][index]:.2f} |"
            f" {'是' if flagged[index] else '否'} |"
        )
    lines.append("")

    DOCS.mkdir(exist_ok=True)
    (DOCS / "Jev自检.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (DOCS / "Jev自检.json").write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cases": len(samples),
                "threshold": THRESHOLD,
                "detected": detected,
                "false_alarms": sum(false_alarms),
                "control": {"min": controls["min"], "mean": controls["mean"], "scores": controls["scores"]},
                "mutation": {"min": mutations["min"], "mean": mutations["mean"], "scores": mutations["scores"]},
                "cost_usd": round(cost, 6),
                "samples": samples,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"注入检出 {detected}/{len(samples)}｜对照误报 {sum(false_alarms)}/{len(samples)}"
          f"｜支持度 对照 {controls['mean']:.2f} vs 注入 {mutations['mean']:.2f}｜${cost:.5f}")
    print(f"报告：{DOCS / 'Jev自检.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
