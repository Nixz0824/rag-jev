"""Routing-evaluation integrity tests.

These exist because the evaluation silently measured nothing once already: the fixture was
keyed by question text, the pipeline rewrote the text before it reached the router, and every
case fell through to the stub's default answer. The run still produced a report — with zeros
that looked like a finding. A guard test is cheaper than discovering that again.

What is checked here is the *evaluation*, not the pipeline (``tests/test_routing.py`` covers
behaviour, and ``docs/Jev分层路由.md`` carries the numbers).
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import patch_engine  # noqa: E402
import routing  # noqa: E402
from config import TESTS  # noqa: E402

CASES = TESTS / "cases" / "routing_cases.json"


class CaseFileTests(unittest.TestCase):
    """The case file is data the report depends on, so its shape is a contract."""

    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
        cls.ids = [case["id"] for case in cls.raw]

    def test_every_case_declares_what_the_router_should_do(self):
        for case in self.raw:
            expect = case["expect"]
            self.assertIn("routing_calls", expect, case["id"])
            self.assertIn("beam_used", expect, case["id"])
            self.assertIn("slots", expect, case["id"])
            self.assertTrue(case.get("why"), f"{case['id']} 缺少 why：案例必须说明它测什么")

    def test_case_ids_are_unique(self):
        self.assertEqual(len(self.ids), len(set(self.ids)))

    def test_the_four_required_query_shapes_are_present(self):
        kinds = {case["kind"] for case in self.raw}
        for kind in ("explicit", "fallback", "lowconfidence", "nokey"):
            self.assertIn(kind, kinds, f"缺少 {kind} 类案例")

    def test_beam_cases_expect_a_fork(self):
        for case in self.raw:
            if case["kind"] in ("fallback", "lowconfidence"):
                self.assertTrue(case["expect"]["beam_used"], f"{case['id']} 应当期望分叉")

    def test_single_ask_cases_expect_one_call_without_a_fork(self):
        # A case where one level is deterministic and one is not: the point is that only the
        # unknown level is asked about, and a clear reading is not turned into a fork.
        cases = [case for case in self.raw if case["kind"] == "single_ask"]
        self.assertTrue(cases, "缺少 single_ask 类案例")
        for case in cases:
            self.assertEqual(case["expect"]["routing_calls"], 1, case["id"])
            self.assertFalse(case["expect"]["beam_used"], case["id"])

    def test_explicit_cases_expect_zero_calls_and_no_fork(self):
        explicit = [case for case in self.raw if case["kind"] == "explicit"]
        self.assertGreaterEqual(len(explicit), 2, "至少要两条全确定案例")
        for case in explicit:
            self.assertEqual(case["expect"]["routing_calls"], 0, case["id"])
            self.assertFalse(case["expect"]["beam_used"], case["id"])

    def test_recorded_distributions_are_probabilities(self):
        for case in self.raw:
            recorded = case["expect"].get("abilities") or []
            for item in recorded:
                self.assertGreaterEqual(item["probability"], 0.0, case["id"])
                self.assertLessEqual(item["probability"], 1.0, case["id"])
                self.assertIn(item["value"], routing.taxonomy.ABILITIES, case["id"])


class ScorableTargetTests(unittest.TestCase):
    """Every case must resolve to a real corpus row, or hit@K measures nothing."""

    @classmethod
    def setUpClass(cls):
        corpus = ROOT / "data" / "patch" / "knowledge.json"
        if not corpus.exists():
            raise unittest.SkipTest("corpus not present (data/patch/knowledge.json is gitignored)")
        cls.retriever = patch_engine.PatchRetriever()
        cls.raw = json.loads(CASES.read_text(encoding="utf-8"))["cases"]

    def test_every_case_resolves_to_a_corpus_row(self):
        from evaluate_routing import resolve_row

        for case in self.raw:
            spec = case["expect"].get("rows")
            self.assertIsNotNone(spec, f"{case['id']} 没有可判定的目标行，命中率将无法度量")
            target = resolve_row(self.retriever, spec)
            self.assertIsNotNone(target, f"{case['id']} 的目标行在语料里不存在：{spec}")

    def test_the_fixture_answers_from_the_recorded_distribution(self):
        """The regression this file exists for: an id-keyed fixture must actually be read."""
        from evaluate_routing import install_fixture

        cases = {case["id"]: case for case in self.raw}
        model = install_fixture(cases)
        try:
            for case in self.raw:
                recorded = case["expect"].get("abilities") or []
                if not recorded:
                    continue
                model.current = case["id"]
                expected = max(recorded, key=lambda item: item["probability"])["value"]
                answer = model.classify_many(case["question"], {"ability": {"E": "E", "W": "W"}})
                self.assertEqual(answer["slots"]["ability"]["choice"], expected, case["id"])
                self.assertEqual(set(answer["slots"]["ability"]["probabilities"]),
                                 {item["value"] for item in recorded}, case["id"])
        finally:
            import jev

            jev.classify_many = jev.classify_many  # restored by the caller's process exit

    def test_an_unknown_current_case_falls_back_instead_of_crashing(self):
        from evaluate_routing import install_fixture

        model = install_fixture({})
        model.current = "nope"
        answer = model.classify_many("任意问题", {"ability": {"E": "E"}})
        self.assertEqual(answer["slots"]["ability"]["choice"], "E")


class ReportShapeTests(unittest.TestCase):
    """If the report has been generated, it must carry the numbers it claims to."""

    def test_generated_report_declares_its_source(self):
        path = ROOT / "docs" / "Jev分层路由.json"
        if not path.exists():
            raise unittest.SkipTest("routing report not generated yet")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn(payload["source"], ("live", "fixture"))
        self.assertIn("arms", payload)
        self.assertIn("behaviour", payload)
        # Negative results must be present, not omitted when they are zero.
        for key in ("improved", "hurt", "recall_gained", "recall_lost", "changed"):
            self.assertIn(key, payload)
        for mode in payload["modes"]:
            self.assertIn(mode, payload["arms"])


if __name__ == "__main__":
    unittest.main()
