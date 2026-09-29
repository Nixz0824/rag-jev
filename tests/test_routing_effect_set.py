"""Contract checks for the routing effect case set.

This set is the sole evidence behind the claim "hierarchical routing gains +3–4 of 40", so its
structural assumptions have to be enforced rather than trusted. Three failure modes actually
happened during development and are pinned here:

1. a case whose quoted value cannot be found in the corpus → it fails in every scope and
   silently pads the denominator (5 cases did exactly that before `resolve_row` was fixed);
2. a case whose skill slot the deterministic rules already close → routing is never asked, so
   the case cannot discriminate and must not be counted as evidence either way;
3. a hand-written probability distribution → the fixture arm replays the author's intended
   answer, which is how the first version produced a "gain" the real model did not reproduce.

The set is also required to keep enough headroom to measure anything: a case whose correct row
already ranks first without narrowing has no room for routing to help.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import patch_engine  # noqa: E402
from evaluate_routing import resolve_row  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases" / "routing_effect_cases.json"
ALLOWED_ABILITIES = {"Q", "W", "E", "R", "被动", "base"}


class EffectCaseFileTests(unittest.TestCase):
    """Shape and vocabulary: a broken file would fail the runner rather than the claim."""

    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(CASES.read_text(encoding="utf-8"))
        cls.cases = cls.payload["cases"]

    def test_the_set_is_large_enough_to_measure_anything(self):
        # The brief asked for 40; a set this size still cannot support a percentage claim, but
        # anything much smaller cannot separate a real gain from model noise.
        self.assertGreaterEqual(len(self.cases), 35)

    def test_every_case_declares_the_effect_kind(self):
        kinds = {case["kind"] for case in self.cases}
        self.assertEqual(kinds, {"routing_effect"})

    def test_ids_are_unique_and_abilities_field_keys_are_known(self):
        import patch_schema  # noqa: E402

        known_fields = {key for key, _ in patch_schema.FIELDS}
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(set(ids)), len(ids))
        for case in self.cases:
            rows = case["expect"]["rows"]
            self.assertIn(rows["ability"], ALLOWED_ABILITIES, case["id"])
            self.assertIn(rows["field_key"], known_fields, case["id"])
            self.assertTrue(rows.get("value"), case["id"])

    def test_both_patch_and_subject_are_reasonably_spread(self):
        patches = {case["expect"]["rows"]["patch"] for case in self.cases}
        subjects = {case["expect"]["rows"]["subject"] for case in self.cases}
        self.assertGreaterEqual(len(patches), 10)
        self.assertGreaterEqual(len(subjects), 20)


class EffectCaseRunnabilityTests(unittest.TestCase):
    """The three failure modes that actually occurred while building this set."""

    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(CASES.read_text(encoding="utf-8"))
        cls.cases = cls.payload["cases"]
        cls.retriever = patch_engine.PatchRetriever()
        cls.engine = patch_engine.PatchEngine(cls.retriever, use_jev=False, retrieval_mode="bm25")

    def test_every_expected_row_resolves_to_a_corpus_row(self):
        unresolvable = [case["id"] for case in self.cases
                        if not resolve_row(self.retriever, case["expect"]["rows"])]
        self.assertEqual(unresolvable, [],
                         f"这些案例的目标行在语料里找不到，任何口径下都只能判错：{unresolvable}")

    def test_no_case_records_a_hand_written_distribution(self):
        offenders = [case["id"] for case in self.cases if case["expect"].get("abilities")]
        self.assertEqual(offenders, [],
                         f"手写分布会让 fixture 口径自证其说：{offenders}")

    def test_the_skill_slot_stays_open_for_almost_every_case(self):
        # Routing narrows on the skill axis. A case whose skill the rules already fix never
        # reaches the model, so it measures nothing about routing.
        closed = [case["id"] for case in self.cases
                  if self.engine.plan(case["question"]).value("ability")]
        self.assertLessEqual(len(closed), len(self.cases) // 10,
                             f"技能槽被规则关闭的案例过多（这些题测不到技能消歧）：{closed}")

    def test_enough_cases_have_headroom_for_routing_to_matter(self):
        """At least a quarter of the set must start with the answer below rank 1.

        A case whose correct row is already first cannot show a gain or a loss, so a set made
        of them measures nothing — which is exactly why the first 8-case set reported no
        difference. Narrowing to the declared skill is the best case routing can achieve.
        """
        discriminating = []
        for case in self.cases:
            rows = case["expect"]["rows"]
            target = resolve_row(self.retriever, rows)
            base = {"patches": [rows["patch"]], "subject": rows["subject"]}
            keys = (self.engine.plan(case["question"]).get("field_keys") or {}).value or []
            if keys:
                base["field_keys"] = list(keys)
            hits = self.retriever.search(case["question"], mode="bm25",
                                         k=len(self.retriever.chunks), where=base)
            rank = next((index for index, row in enumerate(hits, 1) if row["id"] == target), None)
            if rank is None or rank > 1:
                discriminating.append(case["id"])
        self.assertGreaterEqual(
            len(discriminating), len(self.cases) // 4,
            f"只有 {len(discriminating)} 条案例的正确行不在第 1 名，"
            f"这套题测不出路由的作用：{discriminating}")


if __name__ == "__main__":
    unittest.main()
