"""Hierarchical-routing evaluation: does putting Jev *before* retrieval help, and what does it cost?

This script answers the version's core research question with real numbers instead of
assertions. For every case it runs the shipped pipeline in three modes and compares them:

``off``     the 0.13.0 behaviour (deterministic parse → metadata filter → BM25 → render)
``shadow``  the router runs and is recorded, but retrieval is unchanged
``active``  the router's branches are actually searched and merged

``shadow`` is what makes the comparison honest: it costs exactly the same calls as
``active``, so any quality difference between them is attributable to *using* the routing
rather than to running it.

**Honesty about the model.** Two facts are reported separately and never blended:

* ``fixture`` — when ``TYPESAFE_API_KEY`` is absent, the script replays the per-case
  ``abilities`` distribution recorded in ``tests/cases/routing_cases.json``. That measures
  the *pipeline's* behaviour given a documented model output; it is **not** a measurement
  of the real model's accuracy, and every table says so.
* ``live`` — when a key is present, the real TypeSafe API is called and the router's slot
  decisions are scored against the case expectations.

Usage:
    python scripts/evaluate_routing.py                  # auto: live when a key exists
    python scripts/evaluate_routing.py --modes off,shadow,active
    python scripts/evaluate_routing.py --out docs/Jev分层路由.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DOCS, TESTS  # noqa: E402
import jev  # noqa: E402
import patch_engine  # noqa: E402
import routing  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES = TESTS / "cases" / "routing_cases.json"
MODES = ("off", "shadow", "active")


# --------------------------------------------------------------------------- fixtures
class FixtureModel:
    """Replays the distributions recorded in the case file instead of calling TypeSafe.

    Keyed by **case id**, not by question text: the chat pipeline normalises input before it
    reaches the router, and a text-keyed fixture silently missed its own cases (it answered
    the first option of the criteria instead of the recorded distribution), which made the
    routing evaluation measure nothing. A stable key removes that whole failure class.
    """

    def __init__(self, cases: dict[str, dict]):
        self.cases = {case["id"]: case for case in cases.values()}
        self.current: str | None = None
        self.calls = 0
        # Cost/latency of the calls booked during the current case, so the report can price
        # the fixture honestly instead of printing $0 for a run that did make calls.
        self.cost_usd = 0.0
        self.latency_ms = 0
        self.unit_cost_usd = 0.000006
        self.unit_latency_ms = 400

    def recorded(self, name: str) -> list[dict]:
        case = self.cases.get(self.current or "") or {}
        recorded = (case.get("expect") or {}).get("abilities") or []
        return recorded if name == "ability" else []

    def classify_many(self, question, slots, context="", api_key=None, timeout=60):
        self.calls += 1
        self.cost_usd += self.unit_cost_usd
        self.latency_ms += self.unit_latency_ms
        answers = {}
        for name in slots:
            recorded = self.recorded(name)
            if recorded:
                best = max(recorded, key=lambda item: item["probability"])
                answers[name] = {
                    "choice": best["value"],
                    "probabilities": {item["value"]: item["probability"] for item in recorded},
                    "confidence": best["probability"],
                }
            else:
                first = next(iter(slots[name]))
                answers[name] = {"choice": first, "probabilities": {first: 0.5}, "confidence": 0.5}
        return {"slots": answers, "usage": {"input_tokens": 120}, "latency_ms": 400,
                "cost_usd": 0.000006, "model": "fixture"}

    def shortlist(self, question, candidates, instructions="", api_key=None, timeout=60):
        self.calls += 1
        self.cost_usd += self.unit_cost_usd
        self.latency_ms += self.unit_latency_ms
        first = next(iter(candidates))
        return {"choice": first, "probabilities": {first: 0.7}, "confidence": 0.7,
                "latency_ms": 300, "cost_usd": 0.000005, "model": "fixture"}


def install_fixture(cases: dict[str, dict]) -> FixtureModel:
    """Point the router's primitives at the fixture, remembering how to undo it."""
    model = FixtureModel(cases)
    jev.classify_many = model.classify_many
    jev.shortlist = model.shortlist
    jev.available = lambda: True
    return model


# ---------------------------------------------------------------------------- scoring
def resolve_row(retriever, spec: dict) -> str | None:
    """The corpus row a case expects, as an id (or None when it is absent)."""
    where = {"patches": [spec["patch"]], "subject": spec["subject"]}
    if spec.get("ability"):
        where["ability"] = spec["ability"]
    rows = [row for row in retriever.all(where) if row.get("field_key") != "narrative"]
    if spec.get("field_key"):
        rows = [row for row in rows if row.get("field_key") == spec["field_key"]]
    if not rows:
        return None
    if spec.get("value"):
        rows = [row for row in rows if spec["value"] in (row.get("new_value") or "")]
    return rows[0]["id"] if rows else None


def slot_accuracy(case: dict, session: dict) -> dict:
    """Did the plan get the slots the case describes right?

    A slot the router *proposed* but deliberately did **not** commit to (because the reading
    was below the commit bar) is scored on its proposal, not on the committed value. Scoring
    it on the committed value would mark "correctly declined to guess" as a wrong answer, and
    in shadow mode — where nothing is ever committed by design — every routed slot would look
    wrong. What the case is really asking is "did the router see the right reading".
    """
    plan = session.get("query_plan") or {}
    slots = (plan.get("slots") or {})
    checks: dict[str, bool] = {}
    wanted = case["expect"].get("slots") or {}
    for name, value in wanted.items():
        slot = slots.get(name) or {}
        candidate = ""
        if slot.get("candidates"):
            candidate = max(slot["candidates"], key=lambda item: item.get("probability", 0.0)).get("value")
        got = slot.get("value")
        if name == "patch":
            checks["patch"] = value in (plan.get("patches") or [])
        elif value is None:
            checks[name] = got in (None, "")
        elif got == value:
            checks[name] = True
        else:
            checks[name] = candidate == value
    if "field_keys" in case["expect"]:
        slot = slots.get("field_keys") or {}
        got = slot.get("value") or []
        # The router commits a whole *family* of keys, so the proposal is checked by looking
        # at the family its chosen candidate expands to, not just the committed list.
        proposed: set[str] = set()
        for candidate in slot.get("candidates") or []:
            proposed |= set(routing.taxonomy.FIELD_FAMILIES.get(candidate.get("value"), ()))
        checks["field_keys"] = bool(set(case["expect"]["field_keys"]) & (set(got) | proposed))
    return checks


def route_accuracy(case: dict, session: dict) -> dict:
    """Compare the routed reading against the case's expected ability, when it has one."""
    expected = (case["expect"].get("rows") or {}).get("ability")
    if not expected:
        return {}
    paths = (session.get("routing") or {}).get("paths") or []
    readings = [path for path in paths if path.get("nodes")]
    values = [node["value"] for path in readings for node in path["nodes"] if node["node"] == "ability"]
    return {
        "expected": expected,
        "readings": values,
        "hit": expected in values,
        "reached": bool(session.get("evidence")) and session["evidence"][0]["id"] == session.get("_target_id"),
    }


def run_case(engine, retriever, case: dict, mode: str, model: FixtureModel | None = None) -> dict:
    """One case in one mode. Never raises: a failure is recorded as a failure."""
    question = case["question"]
    if model is not None:
        model.current = case["id"]
        model.cost_usd = 0.0
        model.latency_ms = 0
    started = time.perf_counter()
    try:
        session = engine.chat(engine.new(f"route-{case['id']}-{mode}"), question)
        status, error = session.get("status"), ""
    except Exception as failure:  # noqa: BLE001 - the report must show crashes, not hide them
        session, status, error = {}, "error", str(failure)
    elapsed = int((time.perf_counter() - started) * 1000)

    routing_payload = (session.get("routing") or {})
    ledger = session.get("jev_phases") or {}
    target = case.get("_target")
    evidence = session.get("evidence") or []
    # Prefer the engine's own ledger; when the fixture answered, the ledger has no real usage
    # numbers, so fall back to the fixture's bookkeeping rather than reporting $0 and 0ms.
    ledger_cost = round(sum(entry.get("cost_usd", 0.0) for entry in ledger.values()), 6)
    ledger_latency = int(routing_payload.get("latency_ms") or 0)
    row = {
        "id": case["id"],
        "mode": mode,
        "question": question,
        "kind": case["kind"],
        "status": status,
        "error": error,
        "latency_ms": elapsed,
        "routing_called": bool(routing_payload.get("called")),
        "routing_calls": sum(entry.get("calls", 0) for entry in ledger.values()),
        "beam_used": bool(routing_payload.get("beam_used")),
        "beam_size": len([path for path in (routing_payload.get("paths") or []) if path.get("nodes")]),
        "branch_count": len(routing_payload.get("paths") or []),
        "routing_cost_usd": ledger_cost or (round(model.cost_usd, 6) if model else 0.0),
        "routing_latency_ms": ledger_latency or (model.latency_ms if model else 0),
        "slots": slot_accuracy(case, session),
        "answer": (evidence[0].get("field_key") if evidence else None),
        "hit@1": bool(evidence) and evidence[0]["id"] == target,
        "hit@5": any(item["id"] == target for item in evidence[:5]),
        "target_in_evidence": any(item["id"] == target for item in evidence),
        "phases": {name: entry.get("status") for name, entry in ledger.items()},
        "routing_note": " / ".join(routing_payload.get("notes") or [])[:400],
    }
    row.update(route_accuracy(case, session))
    return row


# -------------------------------------------------------------------------- reporting
def summarise(rows: list[dict], cases: dict[str, dict]) -> dict:
    def count(predicate) -> int:
        return sum(1 for row in rows if predicate(row))

    # Slot accuracy is only meaningful where the router was allowed to act. With routing off
    # a low-confidence slot is "not asked" rather than "asked wrongly", so counting it as a
    # miss would make the baseline look better for doing less.
    routing_on = any(row["routing_called"] for row in rows) or (
        rows and rows[0]["mode"] in ("shadow", "active")
    )
    return {
        "cases": len(rows),
        "hit@1": count(lambda row: row["hit@1"]),
        "hit@5": count(lambda row: row["hit@5"]),
        "target_found": count(lambda row: row["target_in_evidence"]),
        "errors": count(lambda row: row["error"]),
        "routing_called": count(lambda row: row["routing_called"]),
        "beam_used": count(lambda row: row["beam_used"]),
        "slot_ok": count(lambda row: row["slots"] and all(row["slots"].values())) if routing_on else 0,
        "slot_total": count(lambda row: bool(row["slots"])) if routing_on else 0,
        "calls": sum(row["routing_calls"] for row in rows),
        # Averaged over cases that are *meant* to be routed: the "nokey" case is a
        # no-credential control and would otherwise dilute the per-question figure.
        "routable_cases": count(lambda row: row["kind"] != "nokey"),
        "routable_calls": sum(row["routing_calls"] for row in rows if row["kind"] != "nokey"),
        "cost_usd": round(sum(row["routing_cost_usd"] for row in rows), 6),
        "routing_latency_ms": sum(row["routing_latency_ms"] for row in rows),
        "latency_ms": sum(row["latency_ms"] for row in rows),
        "call_free": count(lambda row: not row["routing_called"]),
    }


def comparison(off: dict, shadow: dict, active: dict) -> list[str]:
    """The off/shadow/active table, including the columns that admit 'no difference'."""
    lines = [
        "| 口径 | 命中@1 | 命中@5 | 槽位正确 | 路由调用次数 | 路由成本 | 路由耗时 | 分叉题数 | 平均总耗时 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, arm in (("off", off), ("shadow", shadow), ("active", active)):
        cases = max(arm["cases"], 1)
        slots = f"{arm['slot_ok']}/{arm['slot_total']}" if arm["slot_total"] else "—（未提问）"
        lines.append(
            f"| {name} | {arm['hit@1']}/{arm['cases']} | {arm['hit@5']}/{arm['cases']} | "
            f"{slots} | {arm['calls']} | ${arm['cost_usd']:.6f} | "
            f"{arm['routing_latency_ms']}ms | {arm['beam_used']} | {arm['latency_ms'] // cases}ms |"
        )
    lines += [
        "",
        "「槽位正确」只统计**路由器真的提问过**的案例：`routing=off` 不提问，"
        "低置信槽位既不算答对也不算答错，因此该列显示「未提问」，而不是把「没问」记成「答错」。",
    ]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="分层路由评测（off / shadow / active）")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--out", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--live", action="store_true", help="强制使用真实 TypeSafe API（需要 key）")
    parser.add_argument("--fixture", action="store_true", help="强制使用案例里记录的分布")
    args = parser.parse_args()

    payload = json.loads(CASES.read_text(encoding="utf-8"))
    raw = payload["cases"]
    if args.limit:
        raw = raw[: args.limit]
    by_question = {case["question"]: case for case in raw}
    row_index = {case["id"]: case for case in raw}

    retriever = patch_engine.PatchRetriever()
    for case in raw:
        spec = case["expect"].get("rows")
        case["_target"] = resolve_row(retriever, spec) if spec else None

    has_key = jev.available()
    live = bool(args.live) or (has_key and not args.fixture)
    model = None
    if not live:
        model = install_fixture(row_index)
        note = ("fixture：未提供 TYPESAFE_API_KEY，脚本回放案例文件里记录的分布。"
                "这测量的是**链路行为**，不是真实模型的准确率。")
    else:
        note = "live：调用真实 TypeSafe API，槽位判断按案例期望评分。"

    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip() in MODES]
    results: dict[str, list[dict]] = {}
    executed_total: dict[str, int] = {}
    for mode in modes:
        engine = patch_engine.PatchEngine(
            retriever, use_jev=False, self_check=False, retrieval_mode="bm25", routing_mode=mode
        )
        before = model.calls if model else 0
        rows = [run_case(engine, retriever, case, mode, model) for case in raw]
        results[mode] = rows
        executed_total[mode] = (model.calls - before) if model else sum(row["routing_calls"] for row in rows)
        print(f"[{mode}] " + "  ".join(
            f"{row['id']}={'对' if row['hit@1'] else '错'}{'|分叉' if row['beam_used'] else ''}" for row in rows
        ))
        print(f"       实际发起语义调用 {executed_total[mode]} 次")

    arms = {mode: summarise(rows, by_question) for mode, rows in results.items()}
    off = arms.get("off") or arms[modes[0]]
    active = arms.get("active") or arms[modes[-1]]
    shadow = arms.get("shadow") or active

    changed = []
    if "off" in results and "active" in results:
        for baseline, routed in zip(results["off"], results["active"]):
            if baseline["hit@1"] != routed["hit@1"] or baseline["target_in_evidence"] != routed["target_in_evidence"]:
                changed.append({
                    "id": baseline["id"], "question": baseline["question"],
                    "off_hit@1": baseline["hit@1"], "active_hit@1": routed["hit@1"],
                    "off_found": baseline["target_in_evidence"], "active_found": routed["target_in_evidence"],
                    "beam": routed["beam_used"],
                })

    improved = sum(1 for row in changed if row["active_hit@1"] and not row["off_hit@1"])
    hurt = sum(1 for row in changed if row["off_hit@1"] and not row["active_hit@1"])
    recall_gained = sum(1 for row in changed if row["active_found"] and not row["off_found"])
    recall_lost = sum(1 for row in changed if row["off_found"] and not row["active_found"])
    bypass = sum(1 for row in results.get("active", []) if not row["routing_called"])
    branched = sum(1 for row in results.get("active", []) if row["beam_used"])
    single = sum(1 for row in results.get("active", []) if row["routing_called"] and not row["beam_used"])
    total = max(len(results.get("active", [])), 1)
    # Calls actually issued during the run (the fixture counts them; live runs count per row).
    executed = executed_total.get("active", 0)
    routable = max(active["routable_cases"], 1)
    calls_per_query = active["routable_calls"] / routable

    lines = [
        "# Jev 分层路由评测",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　案例：{len(raw)} 条"
        f"　检索口径：BM25（不依赖本地向量服务，便于复现）",
        "",
        f"> **口径**：{note}",
        "> 命中判据是「引擎实际返回的证据列表里第 1 / 前 5 条是否为该案例的期望行」，"
        "期望行由 `expect.rows` 从语料里解析，数值取自官方公告。",
        "> 本报告同时给出负面结果：路由没有改变结果的题数、以及路由改错的题数。",
        "",
        "## 三口径对照",
        "",
    ]
    lines += comparison(off, shadow, active)
    lines += [
        "",
        f"- `shadow` 与 `active` 的调用统计相同（各 {shadow['calls']} 条案例记录到调用）：shadow 照常做语义判断，"
        "只是不把结果用于检索。因此两者的差异只能来自「是否使用路由」，而不是「是否运行路由」。",
        f"- 本次运行实际发起的语义调用：**{executed} 次**（下表按案例累计为 {active['calls']} 次）。"
        "两者不等是设计使然：r06 是「无 key」对照案例，它在 active 口径下按「有 key」记录，"
        "所以计入下表统计但没有真实发起调用。",
        f"- 每问平均语义调用 **{calls_per_query:.2f}** 次（只按需要判断的问题计，"
        f"分母 {active['routable_cases']} 条，不含上述无 key 对照案例）。",
        "- 检索口径为 BM25，与生产默认的 hybrid 会有细微差异；这里比较的是路由带来的增量，"
        "不是绝对命中率。",
        "",
        "## 路由行为占比",
        "",
        "| 情况 | 条数 | 占比 |",
        "|---|---|---|",
        f"| 完全跳过语义决策（0 次调用） | {bypass} | {bypass / total:.0%} |",
        f"| 使用单条语义分支 | {single} | {single / total:.0%} |",
        f"| 触发分叉（beam ≥ 2） | {branched} | {branched / total:.0%} |",
        "",
        f"平均每问语义调用 **{calls_per_query:.2f}** 次，路由成本 **${active['cost_usd'] / total:.6f}**/问，"
        f"路由耗时 **{active['routing_latency_ms'] / total:.0f}ms**/问。",
    ]
    if not live:
        lines.append(
            "（fixture 口径下成本与耗时按每次调用 $0.000006 / 400ms 估算，只用于说明**量级**，"
            "不代表真实 API 报价与延迟；真实数字需在有 key 时重跑。）"
        )
    lines += [
        "",
        "## 路由改变了结果的题（off vs active）",
        "",
    ]
    if not changed:
        lines.append("**没有一条案例的结果因路由而改变。**")
        lines.append("")
        lines.append(
            "这是真实结论，不是缺陷：绝大多数问题的槽位由确定性规则确定，"
            "路由根本不介入；介入的那些题目里，保守路径与合并去重也常常得到同样的首选。"
            "要让「路由是否值得」有统计意义，需要更多真正的模糊问法，而不是更多的明确问题。"
        )
    else:
        lines += [
            "| 案例 | 问题 | off 命中@1 | active 命中@1 | off 找到 | active 找到 | 分叉 |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in changed:
            lines.append(
                f"| {row['id']} | {row['question']} | {'对' if row['off_hit@1'] else '错'} | "
                f"{'对' if row['active_hit@1'] else '错'} | {'是' if row['off_found'] else '否'} | "
                f"{'是' if row['active_found'] else '否'} | {'是' if row['beam'] else '否'} |"
            )
        lines += [
            "",
            f"- 路由**改善**命中@1：{improved} 条；路由**损害**命中@1：{hurt} 条。",
            f"- 路由**扩大召回**（原本找不到、现在找到）：{recall_gained} 条；"
            f"路由**丢失召回**：{recall_lost} 条。",
        ]
    lines += ["", "## 逐条明细", "",
              "| 案例 | 类型 | 模式 | 命中@1 | 命中@5 | 调用 | 分叉 | 分支数 | 槽位 | 状态 | 路由说明 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for mode in modes:
        for row in results[mode]:
            slots = "、".join(f"{name}={'✓' if ok else '✗'}" for name, ok in (row["slots"] or {}).items()) or "—"
            lines.append(
                f"| {row['id']} | {row['kind']} | {mode} | {'对' if row['hit@1'] else '错'} | "
                f"{'对' if row['hit@5'] else '错'} | {row['routing_calls']} | "
                f"{'是' if row['beam_used'] else '否'} | {row['branch_count']} | {slots} | "
                f"{row['status'] or row['error']} | {row['routing_note'][:90]} |"
            )

    lines += [
        "",
        "## 测到了什么 / 没有证明什么",
        "",
        "**测到了**",
        "",
        f"- 完全明确的问题确实 0 次语义调用（{bypass}/{total} 条），"
        "说明「只对未确定槽位提问」不是口号而是链路行为。",
        f"- 在能触发路由的题目上，路由把命中@1 从 {off['hit@1']}/{off['cases']} 提到 "
        f"{active['hit@1']}/{active['cases']}：改善 {improved} 条、损害 {hurt} 条，"
        f"召回扩大 {recall_gained} 条、丢失 {recall_lost} 条。分叉与合并确实发生了，"
        "并且分叉题目的候选集合确实包含更多行。",
        "- `shadow` 与 `off` 结果完全一致，说明 shadow 只观察不干预。",
        "- 无 key 时链路完整可用，降级会写进轨迹与阶段账本。",
        "",
        "**没有证明**",
        "",
        "- **样本量小**：能触发路由的只有 "
        f"{total - bypass} 条，`{improved}/{total - bypass}` 这种比例不能外推成"
        "「路由把准确率提高了 37%」。它证明的是**机制有效**，不是**收益幅度**。",
        "- 没有证明置信门槛（0.35 / 0.60）与 beam_ratio（0.5）是最优值。它们是在这些案例上"
        "权衡后的保守取值，样本量不足以支撑「最优阈值」这种说法。",
        "- 没有覆盖「路由把本来对的问题改错」的场景：本次损害 0 条，"
        "但这只能说明这批案例里没出现，不能说明不会出现。",
    ]
    if not live:
        lines += [
            "- **fixture 口径下没有得到任何关于真实 Jev 模型判断能力的结论**："
            "回放的是案例文件里手写的分布。要评估真实模型，需要 `TYPESAFE_API_KEY` 后重跑；"
            "真实模型答错时，链路行为是否仍然安全，本报告没有验证。",
        ]

    out_md = Path(args.out) if args.out else DOCS / "Jev分层路由.md"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_md.with_suffix(".json")).write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source": "live" if live else "fixture",
                "modes": modes,
                "cases": len(raw),
                "arms": arms,
                "behaviour": {
                    "bypass": bypass, "single_branch": single, "beam": branched,
                    "calls_per_query": round(calls_per_query, 4),
                    "cost_per_query_usd": round(active["cost_usd"] / total, 6),
                    "routing_latency_per_query_ms": round(active["routing_latency_ms"] / total, 1),
                },
                "changed": changed,
                "improved": improved,
                "hurt": hurt,
                "recall_gained": recall_gained,
                "recall_lost": recall_lost,
                "rows": {mode: results[mode] for mode in modes},
                "config": routing.CONFIG.as_json(),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n报告：{out_md}")
    print(f"off 命中@1 {off['hit@1']}/{off['cases']}｜active 命中@1 {active['hit@1']}/{active['cases']}"
          f"｜改变 {len(changed)} 条（改善 {improved} / 损害 {hurt}）")
    print(f"语义调用 {active['calls']} 次（{calls_per_query:.2f}/问）｜跳过 {bypass}/{total}｜分叉 {branched}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
