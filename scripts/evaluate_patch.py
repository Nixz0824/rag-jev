"""Offline A/B evaluation: BM25 / vector / hybrid / hybrid+Jev.

Metrics are computed from the engine's own evidence list and rendered answer, so
the numbers describe the shipped pipeline rather than a side experiment.

The case set is author-written against the same corpus (no held-out split), which
is why the report says so on every table.
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

from config import DOCS, TESTS  # noqa: E402
from patch_engine import PatchEngine, PatchRetriever, patch_sort  # noqa: E402
import jev  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES = TESTS / "cases" / "eval_cases.json"
ARMS = (("bm25", False), ("vector", False), ("hybrid", False), ("hybrid+jev", True))
REFUSAL_WORDS = ("没有", "不覆盖", "不在覆盖", "超出", "没有收录", "没认出", "不能", "不做", "只回答", "只收录", "不是")


def matches(row: dict, expect: dict) -> bool:
    for key, value in expect.items():
        if key in ("subjects_any", "patch"):
            continue
        if key == "value_exact":
            if str(row.get("new_value", "")).strip() != value:
                return False
            continue
        if row.get(key) != value:
            return False
    return True


def resolve_target(retriever: PatchRetriever, target: dict) -> dict | None:
    """Find the one corpus row a pinned case is about."""
    hits = []
    for row in retriever.chunks:
        if row["patch"] != target["patch"] or row["subject"] != target["subject"]:
            continue
        if target.get("ability") and row.get("ability") != target["ability"]:
            continue
        if target.get("field_contains") and target["field_contains"] not in row.get("field", ""):
            continue
        if target.get("value_contains") and target["value_contains"] not in row.get("new_value", ""):
            continue
        hits.append(row)
    return hits[0] if hits else None


ASKED_VERSION_RE = re.compile(r"\d{1,2}[.．]\d{1,2}|上个版本|上一版本|当前版本|最新版本|最新版|这个版本|这版本")


def expected_value(case: dict) -> str:
    """The expected new value, accepted either at case level or inside expect."""
    value = case.get("value")
    if not value:
        candidate = (case.get("expect") or {}).get("value")
        value = candidate if isinstance(candidate, str) else ""
    return value


def expected_rows(retriever: PatchRetriever, expect: dict, value: str, strict_field: bool = True) -> list[dict]:
    """Corpus rows that are the answer to a single-target case."""
    needle = value.replace(" ", "")
    out = []
    for row in retriever.chunks:
        if row["patch"] != expect.get("patch") or row["subject"] != expect.get("subject"):
            continue
        if expect.get("ability") and row.get("ability") != expect["ability"]:
            continue
        if strict_field and expect.get("field_key") and row.get("field_key") != expect["field_key"]:
            continue
        if needle and needle not in str(row.get("new_value", "")).replace(" ", ""):
            continue
        out.append(row)
    return out


def corpus_has_value(retriever: PatchRetriever, expect: dict) -> bool:
    """Whether the expectation's number exists in our corpus at all.

    Blind cases are authored from the official announcement, so a miss here means
    the corpus does not contain that change — a data gap, not a system error.
    """
    return bool(expected_rows(retriever, expect, expected_value({"expect": expect}), strict_field=False))


def run_arm(engine: PatchEngine, cases: list[dict], retriever: PatchRetriever, use_case_version: bool = False) -> dict:
    result = {
        "single": {"total": 0, "hit@1": 0, "hit@5": 0, "value_ok": 0, "value_total": 0, "version_ok": 0, "gt_missing": 0},
        "pinned": {"total": 0, "hit@1": 0, "hit@5": 0, "value_ok": 0, "jev_pick_ok": 0, "jev_picks": 0},
        "overview": {"total": 0, "ok": 0, "subject_miss": 0},
        "absent": {"total": 0, "refused": 0},
        "out_of_scope": {"total": 0, "refused": 0},
        "trap": {"total": 0, "ok": 0},
        "selfcheck": {"total": 0, "pass": 0},
        "jev": {"calls": 0, "choice_picked": 0, "choice_hit": 0, "choice_none": 0, "cost_usd": 0.0,
                "latency_total": 0, "latency_calls": 0},
        "details": [],
    }
    for index, case in enumerate(cases):
        # Questions that do not name a version are answered against the UI selection in
        # the real product; --use-case-version simulates the user having picked it.
        selected = None
        if use_case_version and not ASKED_VERSION_RE.search(case["question"]):
            selected = (case.get("expect") or {}).get("patch") or None
        session = engine.chat(engine.new(f"eval-{index}"), case["question"], selected_patch=selected)
        evidence = session.get("evidence") or []
        answer = "\n".join(m["text"] for m in session["messages"] if m["role"] == "assistant")
        expect = case.get("expect") or {}
        detail = {"id": case["id"], "kind": case["kind"], "status": session["status"]}
        for step in session.get("trace") or []:
            parts = step.get("detail", "")
            if step["tool"] == "Jev 重排" and "ms" in parts:
                result["jev"]["calls"] += 1
        result["jev"]["cost_usd"] += session.get("cost_usd", 0.0)
        if session.get("jev"):
            result["jev"]["latency_total"] += session["jev"].get("latency_ms", 0)
            result["jev"]["latency_calls"] += 1
        check = session.get("self_check")
        if check:
            result["selfcheck"]["total"] += 1
            result["selfcheck"]["pass"] += int(check["min"] >= 0.5)
            detail["self_check_min"] = check["min"]

        if case["kind"] == "pinned":
            result["pinned"]["total"] += 1
            target = resolve_target(retriever, case["target"])
            if target is None:
                detail["error"] = "目标行未在语料中找到"
            else:
                ids = [row["id"] for row in evidence]
                detail["rank"] = ids.index(target["id"]) + 1 if target["id"] in ids else None
                if detail["rank"] == 1:
                    result["pinned"]["hit@1"] += 1
                if detail["rank"] and detail["rank"] <= 5:
                    result["pinned"]["hit@5"] += 1
                needle = case["target"].get("value_contains") or target.get("new_value", "")
                detail["value_ok"] = bool(needle) and needle in answer
                result["pinned"]["value_ok"] += int(bool(detail["value_ok"]))
                pick = (session.get("jev") or {}).get("choice_id")
                if pick:
                    result["pinned"]["jev_picks"] += 1
                    detail["jev_pick"] = "target" if pick == target["id"] else "other"
                    result["pinned"]["jev_pick_ok"] += int(pick == target["id"])
        elif case["kind"] == "single":
            value = expected_value(case)
            if value and not corpus_has_value(retriever, expect):
                result["single"]["gt_missing"] += 1
                detail["ground_truth"] = "missing_in_corpus"
                result["details"].append(detail)
                continue
            result["single"]["total"] += 1
            targets = expected_rows(retriever, expect, value, strict_field=True) if value else []
            field_note = ""
            if value and not targets:
                targets = expected_rows(retriever, expect, value, strict_field=False)
                if targets:
                    field_note = f"语料字段键为 {targets[0].get('field_key')}"
            target_ids = {row["id"] for row in targets}
            if target_ids:
                ids = [row["id"] for row in evidence]
                ranked = [ids.index(row_id) + 1 for row_id in target_ids if row_id in ids]
                if ranked:
                    if min(ranked) == 1:
                        result["single"]["hit@1"] += 1
                    if min(ranked) <= 5:
                        result["single"]["hit@5"] += 1
                    detail["rank"] = min(ranked)
                else:
                    detail["rank"] = None
            else:
                ranked = [i for i, row in enumerate(evidence, start=1) if matches(row, expect)]
                if ranked:
                    result["single"]["hit@1"] += int(ranked[0] == 1)
                    result["single"]["hit@5"] += int(ranked[0] <= 5)
                detail["rank"] = ranked[0] if ranked else None
            if field_note:
                detail["field_key_note"] = field_note
            asked = bool(ASKED_VERSION_RE.search(case["question"]))
            detail["version_asked"] = asked
            if asked and evidence:
                ok = evidence[0].get("patch") == expect.get("patch")
                result["single"]["version_ok"] += int(ok)
                detail["version_ok"] = ok
            if value:
                result["single"]["value_total"] += 1
                compact = answer.replace(" ", "").replace("\u3000", "")
                ok = value.replace(" ", "") in compact
                result["single"]["value_ok"] += int(ok)
                detail["value_ok"] = ok
        elif case["kind"] == "overview":
            result["overview"]["total"] += 1
            wanted = expect.get("subjects_any") or []
            ok = any(subject in answer for subject in wanted)
            result["overview"]["ok"] += int(ok)
            if not ok:
                result["overview"]["subject_miss"] += 1
            detail["overview_ok"] = ok
        elif case["kind"] in ("absent", "out_of_scope"):
            bucket = result[case["kind"]]
            bucket["total"] += 1
            refused = session["status"] in ("abstained", "clarifying") and any(word in answer for word in REFUSAL_WORDS)
            bucket["refused"] += int(refused)
            detail["refused"] = refused
        elif case["kind"] == "trap":
            result["trap"]["total"] += 1
            expect = case.get("expect") or {}
            behavior = expect.get("behavior", "refuse")
            refused = session["status"] in ("abstained", "clarifying")
            if behavior == "refuse":
                ok = refused
            elif behavior == "correct":
                opposite = expect.get("opposite", "")
                ok = refused or opposite in answer
                claimed = "削弱" if opposite == "加强" else "加强"
                if not refused and claimed in answer and opposite not in answer:
                    ok = False
            else:
                ok = False
            if ok and expect.get("mentions"):
                ok = expect["mentions"] in answer
            result["trap"]["ok"] += int(ok)
            detail["trap_ok"] = ok
        result["details"].append(detail)
    return result


def summarise(name: str, arm: dict) -> list[str]:
    single, overview, pinned = arm["single"], arm["overview"], arm["pinned"]
    lines = [f"### {name}", ""]
    if pinned["total"]:
        line = (
            f"- 定点问题 {pinned['total']} 条（目标行前后各有多条候选）：命中@1 **{pinned['hit@1']}/{pinned['total']}**、"
            f"命中@5 **{pinned['hit@5']}/{pinned['total']}**、答案数值正确 **{pinned['value_ok']}/{pinned['total']}**"
        )
        if pinned["jev_picks"]:
            line += f"、Jev 首选命中 **{pinned['jev_pick_ok']}/{pinned['jev_picks']}**"
        lines.append(line)
    if single["total"]:
        asked = sum(1 for detail in arm["details"] if detail.get("kind") == "single" and detail.get("version_asked"))
        single["version_asked"] = asked
        version_note = f"（其中 {asked} 条问题写了版本，按问题判定版本正确性）"
        lines.append(
            f"- 单点问题 {single['total']} 条{version_note}：命中@1 **{single['hit@1']}/{single['total']}**、"
            f"命中@5 **{single['hit@5']}/{single['total']}**、"
            f"版本正确 **{single['version_ok']}/{asked}**、"
            f"数值正确 **{single['value_ok']}/{single['value_total']}**"
        )
    if single.get("gt_missing"):
        lines.append(
            f"- 语料缺口：{single['gt_missing']} 条案例的预期值在语料里找不到（不计入上面的分母，"
            "说明该改动没被收录或与国服公告数值不同）"
        )
    if overview["total"]:
        lines.append(f"- 汇总问题 {overview['total']} 条：答案含预期对象 **{overview['ok']}/{overview['total']}**")
    for kind, label in (("absent", "本版无记录"), ("out_of_scope", "越界/超范围")):
        bucket = arm[kind]
        if bucket["total"]:
            lines.append(f"- {label} {bucket['total']} 条：正确拒答/追问 **{bucket['refused']}/{bucket['total']}**")
    if arm["trap"]["total"]:
        lines.append(
            f"- 陷阱题 {arm['trap']['total']} 条（规则定义，与语料不同源）：符合预期 **{arm['trap']['ok']}/{arm['trap']['total']}**"
        )
    jev_stats = arm["jev"]
    if arm["selfcheck"]["total"]:
        check = arm["selfcheck"]
        lines.append(
            f"- 回答自检（Jev noul）：{check['total']} 条回答，最低支持度 ≥ 0.5 的 **{check['pass']}/{check['total']}**"
        )
    if jev_stats["calls"]:
        lines.append(
            f"- Jev：调用 {jev_stats['calls']} 次，合计 ${jev_stats['cost_usd']:.5f}"
            f"（均 ${jev_stats['cost_usd'] / jev_stats['calls']:.6f}）"
        )
    lines.append("")
    return lines


def findings(results: dict) -> list[str]:
    """Derive the headline statements from the numbers instead of asserting them."""
    lines = []
    baseline = results.get("hybrid") or results.get("bm25")
    jev_arm = results.get("hybrid+jev")
    arms = [name for name in ("bm25", "vector", "hybrid") if name in results]
    if baseline and len(arms) > 1:
        same = all(
            results[name]["single"]["hit@1"] == baseline["single"]["hit@1"]
            and results[name]["pinned"]["hit@1"] == baseline["pinned"]["hit@1"]
            for name in arms
        )
        if same:
            lines.append(
                "- 在元数据过滤之后的候选集合上，BM25 / 向量 / 混合三种排序**指标相同**："
                "候选通常只有 1—6 条，排序方式不再是瓶颈。"
            )
    if baseline and jev_arm:
        delta = jev_arm["pinned"]["hit@1"] - baseline["pinned"]["hit@1"]
        if delta > 0:
            lines.append(
                f"- Jev 重排在**定点问题**上把命中@1 从 {baseline['pinned']['hit@1']}"
                f"/{baseline['pinned']['total']} 提升到 {jev_arm['pinned']['hit@1']}/{jev_arm['pinned']['total']}；"
                "差异来自「问题用词与字段标签不一致」的行（见下方案例明细）。"
            )
        elif delta == 0:
            lines.append("- Jev 重排没有改变本案例集的命中@1；两种口径打平。")
        if jev_arm["jev"]["calls"]:
            mean = jev_arm["jev"]["cost_usd"] / jev_arm["jev"]["calls"]
            lines.append(
                f"- Jev 成本：{jev_arm['jev']['calls']} 次调用合计 ${jev_arm['jev']['cost_usd']:.5f}（均 ${mean:.6f}），"
                "相对本地检索可忽略；失败时会退回 BM25+向量排序并在轨迹里标注。"
            )
    lines.append(
        "- 限制：案例同源、样本量小、差异由个别案例驱动，不能当作真实用户准确率；"
        "要外推需要独立盲测集。"
    )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="检索与回答的离线 A/B 评测")
    parser.add_argument("--arms", default="bm25,vector,hybrid,hybrid+jev")
    parser.add_argument("--cases", default="", help="逗号分隔的案例文件；默认内置两组")
    parser.add_argument("--out", default="", help="报告输出路径；默认 docs/评测报告.md")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--use-case-version",
        action="store_true",
        help="未写版本的题按案例 expect.patch 作为界面版本选择传入（模拟用户在下拉框选中该版本）",
    )
    args = parser.parse_args()

    if args.cases:
        paths = [Path(part.strip()) for part in args.cases.split(",") if part.strip()]
    else:
        paths = [CASES, TESTS / "cases" / "trap_cases.json"]
    cases = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases += payload.get("cases") or []
    if args.limit:
        cases = cases[: args.limit]
    print(f"案例文件：{', '.join(path.name for path in paths)}")

    retriever = PatchRetriever()
    retriever.build()
    print(f"语料 {len(retriever.chunks)} 条，案例 {len(cases)} 条")

    wanted = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    results: dict[str, dict] = {}
    for name, use_jev in ARMS:
        if name not in wanted:
            continue
        if use_jev and not jev.available():
            print(f"跳过 {name}：没有 TYPESAFE_API_KEY")
            continue
        engine = PatchEngine(retriever, use_jev=use_jev, retrieval_mode=name.replace("+jev", ""), self_check=use_jev)
        started = time.perf_counter()
        results[name] = run_arm(engine, cases, retriever, use_case_version=args.use_case_version)
        print(f"{name} 完成，用时 {time.perf_counter() - started:.1f}s")

    lines = [
        "# LoL 版本公告问答 · 离线评测",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　语料：{len(retriever.chunks)} 条（{retriever.supported[0]}—{retriever.latest}）"
        f"　案例：{len(cases)} 条",
        "",
        "> **口径**：案例由作者按当前语料编写，与语料同源，**非盲测、无留出集**；"
        "命中@K 用引擎实际返回的证据列表判定，数值正确性用渲染出的回答文本判定。",
        "",
        "## 关键结论",
        "",
    ]
    lines += findings(results)
    lines.append("")
    for name, arm in results.items():
        lines += summarise(name, arm)
        print("\n".join(summarise(name, arm)))

    lines += ["## 案例明细（hybrid+jev）", "", "| 案例 | 类型 | 状态 | 详情 |", "|---|---|---|---|"]
    best = results.get("hybrid+jev") or next(iter(results.values()))
    for detail in best["details"]:
        bits = []
        if "rank" in detail:
            bits.append(f"命中位次 {detail['rank'] or '未命中'}")
        if "version_ok" in detail:
            bits.append("版本正确" if detail["version_ok"] else "版本不符")
        if "value_ok" in detail:
            bits.append("数值正确" if detail["value_ok"] else "数值不符")
        if "overview_ok" in detail:
            bits.append("含预期对象" if detail["overview_ok"] else "未含预期对象")
        if "refused" in detail:
            bits.append("已拒答" if detail["refused"] else "未拒答")
        if "trap_ok" in detail:
            bits.append("符合预期" if detail["trap_ok"] else "不符合预期")
        if detail.get("ground_truth") == "missing_in_corpus":
            bits.append("**语料缺口**")
        if "self_check_min" in detail:
            bits.append(f"自检 {detail['self_check_min']:.2f}")
        lines.append(f"| {detail['id']} | {detail['kind']} | {detail['status']} | {'；'.join(bits)} |")

    DOCS.mkdir(exist_ok=True)
    report_path = Path(args.out) if args.out else DOCS / "评测报告.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data_path = report_path.with_suffix(".json")
    data_path.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "case_file": [path.name for path in paths],
                "use_case_version": args.use_case_version,
                "corpus": {"chunks": len(retriever.chunks), "patches": retriever.supported},
                "cases": len(cases),
                "arms": results,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n报告：{report_path}\n数据：{data_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
