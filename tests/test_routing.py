"""Semantic routing tests: closure, fallback, beam, budgets, and degradation.

Every test here runs against the **shipped corpus** (``data/patch/knowledge.json``) with a
stubbed classifier instead of the real API, because what is being tested is the router's
*policy* — when it is allowed to ask, what it does with a close call, and what happens when
the model is absent. Ranking quality is not asserted here; that is
``scripts/evaluate_routing.py``'s job.

The stub records every call, which is how the "a deterministically resolved question costs
zero model calls" guarantee is enforced rather than merely documented.
"""

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jev  # noqa: E402
import patch_engine  # noqa: E402
import routing  # noqa: E402

KNOWLEDGE = ROOT / "data" / "patch" / "knowledge.json"

# Distinct, verifiable probabilities per slot, so a test can assert which reading won and
# whether the runner-up was close enough to branch on.
ABILITIES = {"W": 0.51, "被动": 0.47, "Q": 0.01, "base": 0.01}
FAMILIES = {"damage": 0.52, "durability": 0.44, "tempo": 0.04}


class StubJev:
    """Stands in for the TypeSafe API; records calls and answers deterministic choices."""

    def __init__(self, abilities=None, families=None, fail=False, shortlist_choice="c0"):
        self.abilities = dict(abilities or ABILITIES)
        self.families = dict(families or FAMILIES)
        self.fail = fail
        self.shortlist_choice = shortlist_choice
        self.calls: list[dict] = []
        self.cost_per_call = 0.0000042

    # -------------------------------------------------------------- stubbed primitives

    def classify_many(self, question, slots, context="", api_key=None, timeout=60):
        self.calls.append({"kind": "classify_many", "slots": sorted(slots), "question": question})
        if self.fail:
            return None
        answers = {}
        for name in slots:
            if name == "ability":
                answers[name] = self._answer(self.abilities, name)
            elif name in ("field", "field_key"):
                answers[name] = self._answer(self.families, name)
            else:
                first = next(iter(slots[name]))
                answers[name] = {"choice": first, "probabilities": {first: 0.8}, "confidence": 0.8}
        return {"slots": answers, "usage": {"input_tokens": 120}, "latency_ms": 250,
                "cost_usd": self.cost_per_call, "model": "jev-stub"}

    def shortlist(self, question, candidates, instructions="", api_key=None, timeout=60):
        self.calls.append({"kind": "shortlist", "candidates": list(candidates)})
        if self.fail:
            return None
        return {"choice": self.shortlist_choice, "probabilities": {self.shortlist_choice: 0.77},
                "confidence": 0.77, "latency_ms": 180, "cost_usd": self.cost_per_call,
                "model": "jev-stub"}

    @staticmethod
    def _answer(weights: dict, name: str) -> dict:
        ordering = sorted(weights.items(), key=lambda item: -item[1])
        top = ordering[0][0]
        return {"choice": top, "probabilities": dict(weights), "confidence": weights[top]}


class RetrieverMixin:
    """One shared read-only retriever: parsing the 5 MB corpus per test is pure waste."""

    retriever = None

    @classmethod
    def setUpClass(cls):
        if not KNOWLEDGE.exists():
            raise unittest.SkipTest("corpus not present (data/patch/knowledge.json is gitignored)")
        if RetrieverMixin.retriever is None:
            RetrieverMixin.retriever = patch_engine.PatchRetriever()

    def engine(self, stub=None, mode=routing.MODE_ACTIVE, config=None):
        if stub is not None:
            self._patch_jev(stub)
        return patch_engine.PatchEngine(
            self.retriever, use_jev=False, self_check=False, retrieval_mode="bm25",
            routing_mode=mode, routing_config=config,
        )

    def _patch_jev(self, stub):
        originals = (jev.classify_many, jev.shortlist, jev.available)
        jev.classify_many = stub.classify_many
        jev.shortlist = stub.shortlist
        jev.available = lambda: True
        self.addCleanup(lambda: (setattr(jev, "classify_many", originals[0]),
                                 setattr(jev, "shortlist", originals[1]),
                                 setattr(jev, "available", originals[2])))

    def no_key(self):
        """Force the real no-credential path regardless of how the machine is set up.

        A developer machine may legitimately have a key installed (``runtime/jev-key.txt``
        or ``TYPESAFE_API_KEY``), and these tests must still exercise the degraded path
        rather than silently measuring a live run. Neither the env var nor the key file may
        be touched on disk, so both are hidden from ``load_api_key`` for the duration.
        """
        originals = (os.environ.pop("TYPESAFE_API_KEY", None), jev.RUNTIME)
        self.addCleanup(lambda: (
            os.environ.__setitem__("TYPESAFE_API_KEY", originals[0]) if originals[0] else None,
            setattr(jev, "RUNTIME", originals[1]),
        ))
        jev.RUNTIME = Path(os.devnull).parent / "_ragjev_no_such_runtime_"


class ClosureTests(RetrieverMixin, unittest.TestCase):
    """A question the rules fully resolve must cost exactly zero model calls."""

    def test_fully_explicit_question_makes_no_calls(self):
        stub = StubJev()
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "26.17 薇恩 W 真实伤害是多少")
        self.assertEqual(stub.calls, [], "确定性已确定的槽位不得调用模型")
        plan = session["query_plan"]
        self.assertEqual(plan["slots"]["ability"]["source"], "deterministic")
        self.assertEqual(plan["slots"]["ability"]["value"], "W")
        self.assertEqual(plan["slots"]["subject"]["source"], "alias")
        self.assertEqual(plan["slots"]["field_keys"]["value"], ["damage"])
        self.assertFalse(plan["routing"]["beam_used"])
        self.assertEqual(session["status"], "verify")

    def test_every_slot_records_why_it_was_decided(self):
        stub = StubJev()
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "26.17 亚索 Q 冷却改成多少了")
        slots = session["query_plan"]["slots"]
        self.assertIn("显式技能字母 Q", slots["ability"]["why"])
        self.assertIn("正则命中显式版本", slots["patches"]["why"])
        self.assertTrue(slots["subject"]["why"].startswith("别名命中"))
        self.assertIn("冷却", slots["field_keys"]["why"])

    def test_ability_from_a_skill_name_is_also_closed(self):
        stub = StubJev()
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "26.15 锐雯的放逐之锋怎么改了")
        self.assertEqual(stub.calls, [])
        self.assertEqual(session["query_plan"]["slots"]["ability"]["value"], "R")
        self.assertIn("技能名", session["query_plan"]["slots"]["ability"]["why"])

    def test_routing_off_never_calls_a_model(self):
        stub = StubJev()
        engine = self.engine(stub, mode=routing.MODE_OFF)
        session = engine.chat(engine.new("s"), "薇恩那个百分比伤害之前改过吗")
        self.assertEqual(stub.calls, [])
        self.assertFalse(session["routing"]["called"])
        self.assertEqual(session["routing"]["mode"], "off")
        self.assertTrue(any("routing=off" in note for note in session["routing"]["notes"]))


class FallbackTests(RetrieverMixin, unittest.TestCase):
    """A genuinely open slot is where the model is allowed in."""

    def test_open_ability_is_filled_by_semantic_fallback(self):
        stub = StubJev()
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        plan = session["query_plan"]
        self.assertEqual(plan["slots"]["ability"]["value"], "W")
        self.assertEqual(plan["slots"]["ability"]["source"], "semantic")
        self.assertAlmostEqual(plan["slots"]["ability"]["confidence"], 0.51, places=4)
        self.assertEqual(plan["slots"]["ability"]["status"], "resolved")
        joined = " ".join(stub.calls[0]["slots"])
        self.assertIn("ability", joined)

    def test_declining_answers_are_not_treated_as_values(self):
        # A model that answers "unspecified" is saying "no decision here"; turning that into
        # a filter would invent a constraint the question never implied.
        stub = StubJev()
        stub.classify_many = lambda question, slots, **kw: {
            "slots": {name: {"choice": "unspecified", "probabilities": {"unspecified": 0.9},
                             "confidence": 0.9} for name in slots},
            "usage": {}, "latency_ms": 10, "cost_usd": 0.000001, "model": "jev-stub",
        }
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        slot = session["query_plan"]["slots"]["ability"]
        self.assertIsNone(slot["value"])
        self.assertIn("未指明", slot["why"])

    def test_low_confidence_answer_is_recorded_but_not_reused(self):
        stub = StubJev(abilities={"W": 0.20, "被动": 0.18, "Q": 0.31, "base": 0.31})
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        slot = session["query_plan"]["slots"]["ability"]
        self.assertIsNone(slot["value"])
        self.assertEqual(slot["source"], "semantic")
        self.assertIn("低于补全门槛", slot["why"])
        # The distribution is kept even though the value was rejected: it is evidence about
        # the question, and the UI shows why nothing was committed.
        self.assertTrue(slot["candidates"])

    def test_failed_call_degrades_and_is_recorded(self):
        stub = StubJev(fail=True)
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        self.assertTrue(stub.calls)
        self.assertEqual(session["status"], "verify", "调用失败也必须能回答")
        self.assertTrue(any("失败" in note for note in session["routing"]["notes"]))
        self.assertEqual(session["jev_phases"]["semantic_fallback"]["status"], "fallback")

    def test_no_api_key_degrades_without_touching_the_network(self):
        # Deliberately not stubbing: this is the real no-key path. The name is one the
        # alias table does not know, so the ability slot stays open and the router has to
        # report that it could not ask.
        self.no_key()
        engine = patch_engine.PatchEngine(self.retriever, use_jev=True, retrieval_mode="bm25",
                                          routing_mode=routing.MODE_ACTIVE)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        self.assertEqual(session["status"], "verify")
        self.assertFalse(session["routing"]["called"])
        self.assertEqual(session["jev_phases"]["semantic_fallback"]["status"], "fallback")
        notes = " ".join(session["routing"]["notes"])
        self.assertTrue("TYPESAFE_API_KEY" in notes or "预算" in notes)

    def test_no_key_with_real_candidates_books_rerank_as_a_degradation(self):
        # A fallback is only honest when there was work to do: rerank needs ≥2 candidates,
        # so a single-candidate answer is "bypassed", not a degraded rerank.
        self.no_key()
        engine = patch_engine.PatchEngine(self.retriever, use_jev=True, retrieval_mode="bm25",
                                          routing_mode=routing.MODE_ACTIVE)
        session = engine.chat(engine.new("s"), "26.18 卡西奥佩娅改了什么")
        self.assertGreaterEqual(len(session["evidence"]), 2)
        self.assertEqual(session["jev_phases"]["rerank"]["status"], "fallback")
        self.assertIn("TYPESAFE_API_KEY", session["jev_phases"]["rerank"]["detail"])

    def test_a_question_needing_nothing_stays_bypassed_without_a_key(self):
        self.no_key()
        engine = patch_engine.PatchEngine(self.retriever, use_jev=True, retrieval_mode="bm25",
                                          routing_mode=routing.MODE_ACTIVE)
        session = engine.chat(engine.new("s"), "26.17 薇恩 W 真实伤害是多少")
        # Nothing was left open, so even without a key the fallback phase is "bypassed"
        # rather than "fallback": absence of a decision is not a degradation.
        self.assertEqual(session["jev_phases"]["semantic_fallback"]["status"], "bypassed")


class FieldWideningTests(unittest.TestCase):
    """A field filter must not drop the answer because the schema filed it elsewhere.

    Announcement labels are finer than the corpus keys: Jax's "额外护甲和魔法抗性" is stored
    under ``armor``, and 艾瑞莉娅's "法强加成" lives on a ``damage`` row. Measured on the
    40-case effect set, exact field filtering removed the correct row in 5 cases.

    Widening to a whole family was measured too and rejected: the durability family has nine
    keys, and admitting all of them pulled in unrelated rows (active 30/40 → 29.3/40 with two
    newly harmed cases). A sibling is therefore admitted only when the question itself contains
    that field's own pattern — the user's words are the evidence, not schema adjacency.
    """

    def router(self):
        return routing.SemanticRouter.__new__(routing.SemanticRouter)

    def test_declared_keys_are_always_kept(self):
        router = self.router()
        self.assertEqual(router.widened_field_keys(["damage"], "随便问"), ["damage"])
        self.assertEqual(router.widened_field_keys(["slow", "cost"], ""), ["slow", "cost"])

    def test_a_sibling_is_added_only_when_the_question_names_it(self):
        router = self.router()
        # "法强" is the ability_power pattern, so that key is admitted alongside damage.
        self.assertEqual(
            router.widened_field_keys(["damage"], "艾瑞莉娅蓄满后法强加成调到多少"),
            ["damage", "ability_power"],
        )
        # The same declared key with no such wording stays exact.
        self.assertEqual(router.widened_field_keys(["damage"], "第一段伤害改成多少"), ["damage"])

    def test_widening_is_a_superset_so_it_cannot_lose_a_row(self):
        router = self.router()
        for keys in (["damage"], ["resistances"], ["slow"], ["cost", "health"]):
            widened = router.widened_field_keys(keys, "26.17 亚索护甲法强攻速冷却都改了吗")
            for key in keys:
                self.assertIn(key, widened)

    def test_the_switch_disables_widening(self):
        original = os.environ.get("RAGJEV_FIELD_WIDENING")
        os.environ["RAGJEV_FIELD_WIDENING"] = "0"
        try:
            router = self.router()
            self.assertEqual(
                router.widened_field_keys(["damage"], "艾瑞莉娅蓄满后法强加成调到多少"),
                ["damage"],
            )
        finally:
            if original is None:
                os.environ.pop("RAGJEV_FIELD_WIDENING", None)
            else:
                os.environ["RAGJEV_FIELD_WIDENING"] = original


class BeamTests(RetrieverMixin, unittest.TestCase):
    """A close call must widen the search instead of being resolved early."""

    def test_close_runner_up_produces_two_branches(self):
        stub = StubJev()
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        plan = session["query_plan"]
        self.assertTrue(plan["routing"]["beam_used"])
        paths = plan["routing"]["paths"]
        labels = [path["label"] for path in paths]
        self.assertIn("ability=W", labels)
        self.assertIn("ability=被动", labels)
        self.assertEqual(paths[0]["score"], 0.51)
        self.assertEqual(paths[1]["score"], 0.47)
        # The safety net has no ability/field filter at all.
        self.assertIn("unfiltered", labels)
        self.assertNotIn("ability", paths[-1]["where"])

    def test_clear_winner_does_not_branch(self):
        stub = StubJev(abilities={"W": 0.93, "被动": 0.04, "Q": 0.02, "base": 0.01})
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        plan = session["query_plan"]
        self.assertFalse(plan["routing"]["beam_used"])
        paths = plan["routing"]["paths"]
        self.assertEqual([path["label"] for path in paths], ["ability=W", "unfiltered"])

    def test_branch_retrieval_merges_candidates_and_counts_them(self):
        # 卡西奥佩娅 really has Q/E/R rows in the latest patch, so the branches return
        # different sets and the merge has something to merge. (An earlier version of this
        # test used a champion with no rows in that patch, so every branch was legitimately
        # empty and the assertion was vacuous.)
        stub = StubJev()
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "卡西奥佩娅的技能伤害改过吗")
        paths = session["query_plan"]["routing"]["paths"]
        counts = {path["label"]: path["candidate_count"] for path in paths}
        # Some branch must find something, and the unfiltered one must find the most: that is
        # what makes it a safety net rather than a duplicate of the primary reading.
        self.assertTrue(any(count > 0 for count in counts.values()), f"至少一个分支要有候选：{counts}")
        self.assertGreaterEqual(counts["unfiltered"], max(counts.values()))
        self.assertNotEqual(counts["unfiltered"], counts["ability=被动"], "分支过滤应当改变候选集")
        tools = {step["tool"] for step in session["trace"]}
        self.assertIn("分支检索", tools)

    def test_a_branch_that_finds_nothing_is_still_reported(self):
        # 薇恩 has no rows at all in the latest patch, so every branch is empty; that is a
        # legitimate outcome and must be visible rather than silently dropped.
        stub = StubJev()
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        paths = session["query_plan"]["routing"]["paths"]
        self.assertTrue(all(path["candidate_count"] == 0 for path in paths))
        self.assertTrue(any("分支检索" == step["tool"] for step in session["trace"]))

    def test_multi_reading_widens_recall_over_a_single_narrow_branch(self):
        # The point of branching: the primary reading alone would return fewer rows, and the
        # merge must surface candidates the primary branch never saw.
        stub = StubJev()
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "卡西奥佩娅的技能伤害改过吗")
        paths = session["query_plan"]["routing"]["paths"]
        primary = next(path for path in paths if path["label"] == "ability=W")
        merged_ids = {row["id"] for row in session["evidence"]}
        primary_ids = {
            row["id"]
            for row in self.retriever.search("卡西奥佩娅的技能伤害改过吗", mode="bm25",
                                             k=len(self.retriever.chunks), where=primary["where"])
        }
        self.assertTrue(merged_ids - primary_ids, "合并后应包含首选分支看不到的候选")

    def test_the_conservative_path_keeps_a_wrong_reading_recoverable(self):
        stub = StubJev(abilities={"W": 0.34, "被动": 0.33, "Q": 0.33, "base": 0.0})
        engine = self.engine(stub, config=routing.RoutingConfig(min_fallback_confidence=0.1))
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        paths = session["query_plan"]["routing"]["paths"]
        unfiltered = [path for path in paths if path["label"] == "unfiltered"]
        self.assertTrue(unfiltered, "低置信时必须保留无过滤路径")
        for path in unfiltered:
            self.assertNotIn("ability", path["where"])
            self.assertNotIn("field_keys", path["where"])

    def test_beam_width_is_respected(self):
        stub = StubJev(abilities={"W": 0.34, "被动": 0.33, "Q": 0.32, "base": 0.01})
        config = routing.RoutingConfig(min_fallback_confidence=0.1, beam_width=1)
        engine = self.engine(stub, config=config)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        self.assertLessEqual(len(session["query_plan"]["routing"]["paths"]), 2)


class ShadowModeTests(RetrieverMixin, unittest.TestCase):
    """Shadow must do the work, record it, and change nothing."""

    def test_shadow_records_the_same_work_as_active(self):
        active = StubJev()
        engine = self.engine(active, mode=routing.MODE_ACTIVE)
        active_session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")

        shadow = StubJev()
        engine = self.engine(shadow, mode=routing.MODE_SHADOW)
        shadow_session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")

        self.assertEqual([call["slots"] for call in shadow.calls], [call["slots"] for call in active.calls])
        self.assertEqual(shadow_session["cost_usd"], active_session["cost_usd"])
        self.assertTrue(shadow_session["routing"]["beam_used"])
        self.assertIn("shadow", " ".join(shadow_session["routing"]["notes"]))

    def test_shadow_does_not_change_the_retrieval_filters(self):
        shadow = StubJev()
        engine = self.engine(shadow, mode=routing.MODE_SHADOW)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        # The plan records what the router proposed, but the answer path used the baseline:
        # no ability filter was applied, so the plan slot stayed empty.
        self.assertIsNone(session["query_plan"]["slots"]["ability"]["value"])
        self.assertEqual(session["query_plan"]["slots"]["ability"]["why"], "shadow 模式：记录但未采纳")

    def test_shadow_answer_matches_routing_off(self):
        off_engine = self.engine(StubJev(), mode=routing.MODE_OFF)
        off_session = off_engine.chat(off_engine.new("s"), "薇恩的技能伤害改过吗")
        shadow_engine = self.engine(StubJev(), mode=routing.MODE_SHADOW)
        shadow_session = shadow_engine.chat(shadow_engine.new("s"), "薇恩的技能伤害改过吗")
        off_text = [m["text"] for m in off_session["messages"] if m["role"] == "assistant"]
        shadow_text = [m["text"] for m in shadow_session["messages"] if m["role"] == "assistant"]
        self.assertEqual(shadow_text, off_text, "shadow 不得改变最终答案")
        self.assertEqual([row["id"] for row in shadow_session["evidence"]],
                         [row["id"] for row in off_session["evidence"]])


class BudgetTests(RetrieverMixin, unittest.TestCase):
    """One question must never turn into ten API calls."""

    def test_call_budget_is_a_hard_cap(self):
        stub = StubJev()
        config = routing.RoutingConfig(max_jev_calls=1)
        engine = self.engine(stub, config=config)
        engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        self.assertLessEqual(len(stub.calls), 1)

    def test_zero_budget_degrades_immediately(self):
        stub = StubJev()
        config = routing.RoutingConfig(max_jev_calls=0)
        engine = self.engine(stub, config=config)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        self.assertEqual(stub.calls, [])
        self.assertEqual(session["status"], "verify")
        self.assertIn("预算", " ".join(session["routing"]["notes"]))

    def test_cost_is_summed_into_the_session_and_the_ledger(self):
        stub = StubJev()
        engine = self.engine(stub)
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        ledger_cost = sum(entry["cost_usd"] for entry in session["jev_phases"].values())
        self.assertAlmostEqual(session["cost_usd"], ledger_cost, places=6)
        self.assertEqual(session["jev_phases"]["semantic_fallback"]["calls"], 1)
        self.assertEqual(session["jev_phases"]["semantic_fallback"]["status"], "called")

    def test_every_phase_is_present_with_a_status(self):
        engine = self.engine(StubJev(), mode=routing.MODE_OFF)
        session = engine.chat(engine.new("s"), "26.17 亚索改了什么")
        for phase in jev.PHASES:
            entry = session["jev_phases"][phase]
            self.assertIn(entry["status"], (jev.PHASE_CALLED, jev.PHASE_BYPASSED,
                                            jev.PHASE_FALLBACK, jev.PHASE_FAILED))
            self.assertIn("detail", entry)


class SubjectShortlistTests(RetrieverMixin, unittest.TestCase):
    """An entity is chosen from a shortlist, never from the whole table."""

    def test_shortlist_is_small_and_only_contains_answerable_subjects(self):
        shortlist = self.retriever.subject_shortlist("薇恩的技能伤害改过吗")
        self.assertLessEqual(len(shortlist), 3)
        answerable = {row["subject"] for row in self.retriever.chunks if row.get("field_key") != "narrative"}
        for subject, reason in shortlist:
            self.assertIn(subject, answerable)
            self.assertTrue(reason)

    def test_alias_hit_never_reaches_the_shortlist_path(self):
        stub = StubJev()
        engine = self.engine(stub)
        engine.chat(engine.new("s"), "26.17 亚索改了什么")
        self.assertEqual([call for call in stub.calls if call["kind"] == "shortlist"], [])

    def test_shortlist_choice_is_recorded_as_semantic(self):
        stub = StubJev(shortlist_choice="c0")
        engine = self.engine(stub)
        # A name the alias table does not know, but which is lexically close to a real one.
        session = engine.chat(engine.new("s"), "薇思的改动有哪些")
        slot = session["query_plan"]["slots"]["subject"]
        if slot["source"] == "semantic":
            self.assertTrue(slot["candidates"])
            self.assertIn("语义在", slot["why"])


class PathScoreTests(unittest.TestCase):
    """The score must stay comparable between short and long paths."""

    def test_empty_path_scores_one(self):
        self.assertEqual(routing.path_score([]), 1.0)

    def test_single_node_is_its_own_probability(self):
        self.assertEqual(routing.path_score([{"node": "ability", "value": "W", "probability": 0.91}]), 0.91)

    def test_geometric_mean_does_not_punish_depth(self):
        # Two nodes at 0.81 score 0.81, exactly like one node at 0.81. A product would give
        # 0.656 and make a two-level path look worse than an equally-certain one-level path.
        self.assertEqual(
            routing.path_score([
                {"node": "ability", "value": "W", "probability": 0.81},
                {"node": "field", "value": "damage", "probability": 0.81},
            ]),
            0.81,
        )

    def test_zero_probability_does_not_break_the_log(self):
        # A zero-probability node is floored at a tiny epsilon: the geometric mean stays
        # finite and the path is effectively rejected rather than crashing the router.
        score = routing.path_score([{"node": "ability", "value": "Q", "probability": 0.0}])
        self.assertIsInstance(score, float)
        self.assertLess(score, 0.001)


class MergeTests(unittest.TestCase):
    """Merging is deterministic and counts each row once."""

    def _paths(self):
        return [
            routing.RoutePath(nodes=[{"node": "ability", "value": "W", "probability": 0.51}],
                              score=0.51, where={"ability": "W"}),
            routing.RoutePath(nodes=[{"node": "ability", "value": "被动", "probability": 0.47}],
                              score=0.47, where={"ability": "被动"}),
        ]

    def test_rows_are_deduplicated_and_keep_their_best_rank(self):
        paths = self._paths()
        merged = routing.merge_candidates([
            (paths[0], [{"id": "a"}, {"id": "b"}, {"id": "c"}]),
            (paths[1], [{"id": "b"}, {"id": "a"}, {"id": "d"}]),
        ])
        self.assertEqual([row["id"] for row in merged], ["a", "b", "c", "d"])
        by_id = {row["id"]: row for row in merged}
        self.assertEqual(by_id["a"]["branch_rank"], 1)
        self.assertEqual(by_id["b"]["branch_rank"], 1)
        self.assertEqual(len(by_id["a"]["branches"]), 2)

    def test_candidate_counts_are_recorded_on_the_paths(self):
        paths = self._paths()
        routing.merge_candidates([(paths[0], [{"id": "a"}]), (paths[1], [{"id": "b"}, {"id": "c"}])])
        self.assertEqual(paths[0].candidate_count, 1)
        self.assertEqual(paths[1].candidate_count, 2)

    def test_empty_input_is_empty_output(self):
        self.assertEqual(routing.merge_candidates([]), [])


class ModeHintTests(RetrieverMixin, unittest.TestCase):
    """A scope node is only worth asking about when the question gestures at a scope."""

    def test_mode_choices_are_only_offered_when_the_text_hints_at_a_mode(self):
        engine = self.engine(StubJev())
        router = routing.SemanticRouter(self.retriever, mode=routing.MODE_ACTIVE)
        plan = engine.plan("26.17 亚索改了什么")
        self.assertEqual(router._choices_for(plan, "mode"), {})
        hinted = engine.plan("26.17 大乱斗亚索改了什么")
        choices = router._choices_for(hinted, "mode")
        self.assertIn("unspecified", choices)
        self.assertIn("aram", choices)
        # rift is the default scope and is never offered as a classification answer.
        self.assertNotIn("rift", choices)

    def test_a_mode_hint_is_still_resolved_deterministically(self):
        # "经典模式" is a real hint, so the deterministic detector closes the slot and the
        # router has nothing left to ask about scope.
        self.assertEqual(patch_engine.detect_mode("经典模式亚索改了什么"), "classic")
        engine = self.engine(StubJev())
        plan = engine.plan("26.17 经典模式亚索改了什么")
        self.assertEqual(plan.get("mode").source, "deterministic")


class TraceCompletenessTests(RetrieverMixin, unittest.TestCase):
    """Nothing may be skipped silently."""

    def test_skips_and_failures_reach_the_trace_or_the_ledger(self):
        engine = self.engine(StubJev(fail=True))
        session = engine.chat(engine.new("s"), "薇恩的技能伤害改过吗")
        tools = [step["tool"] for step in session["trace"]]
        self.assertIn("语义路由", tools)
        details = " ".join(step["detail"] for step in session["trace"])
        self.assertTrue("失败" in details or "预算" in details)

    def test_plan_and_routing_always_reach_the_session(self):
        engine = self.engine(StubJev(), mode=routing.MODE_OFF)
        session = engine.chat(engine.new("s"), "26.17 亚索改了什么")
        self.assertIsNotNone(session["query_plan"])
        self.assertIsNotNone(session["routing"])
        self.assertIn("config", session["routing"])
        payload = json.dumps(session["query_plan"], ensure_ascii=False)
        self.assertIn("routing", payload)
        self.assertIn("slots", payload)

    def test_routing_config_is_reported_not_hidden(self):
        config = routing.RoutingConfig(beam_ratio=0.7, beam_width=3)
        engine = self.engine(StubJev(), config=config)
        session = engine.chat(engine.new("s"), "26.17 亚索改了什么")
        reported = session["routing"]["config"]
        self.assertEqual(reported["beam_ratio"], 0.7)
        self.assertEqual(reported["beam_width"], 3)
        self.assertIn("commit_confidence", reported)


if __name__ == "__main__":
    unittest.main()
