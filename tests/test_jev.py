"""Jev client tests: no network calls, only pure helpers and injected answers."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jev  # noqa: E402


class HelperTests(unittest.TestCase):
    def test_compact_collapses_whitespace_and_truncates(self):
        self.assertEqual(jev.compact("  亚索   Q  "), "亚索 Q")
        self.assertTrue(jev.compact("x" * 500).endswith("…"))
        self.assertLessEqual(len(jev.compact("x" * 500)), jev.MAX_CANDIDATE_CHARS + 1)

    def test_candidate_state_labels_every_hit(self):
        hits = [{"id": "a", "text": "第一条"}, {"id": "b", "text": "第二条"}]
        state, labels = jev.candidate_state("问题", hits)
        self.assertIn("i1: 第一条", state)
        self.assertIn("i2: 第二条", state)
        self.assertEqual(labels, {"i1": "a", "i2": "b"})

    def test_candidate_state_caps_the_list(self):
        hits = [{"id": str(index), "text": "t"} for index in range(jev.MAX_CANDIDATES + 5)]
        _, labels = jev.candidate_state("问题", hits)
        self.assertEqual(len(labels), jev.MAX_CANDIDATES)

    def test_cost_estimate_uses_input_tokens_only(self):
        self.assertAlmostEqual(jev.estimate_cost_usd({"input_tokens": 1_000_000}), 0.042, places=6)
        self.assertEqual(jev.estimate_cost_usd({}), 0.0)


class RerankTests(unittest.TestCase):
    hits = [
        {"id": "wrong", "text": "26.16 亚索 Q 伤害 18 → 20", "rank": 1},
        {"id": "right", "text": "26.17 亚索 Q 冷却时间 4 → 3.5", "rank": 2},
        {"id": "other", "text": "26.17 岚切 攻击速度 20% → 25%", "rank": 3},
    ]

    def setUp(self):
        self.calls = []

        def fake_ask(state, questions, **kwargs):
            self.calls.append((state, questions))
            return {
                "answers": {
                    "pick": {"choice": "i2", "confidence": 0.9, "probabilities": {"i1": 0.05, "i2": 0.9, "i3": 0.05}},
                    "rel_i1": {"noul": 0.03},
                    "rel_i2": {"noul": 0.81},
                    "rel_i3": {"noul": 0.02},
                },
                "usage": {"input_tokens": 900},
                "model": "jev-test",
                "latency_ms": 120,
                "cost_usd": 0.000038,
            }

        self.original = jev.ask
        jev.ask = fake_ask

    def tearDown(self):
        jev.ask = self.original

    def test_rerank_orders_by_noul_and_reports_the_choice(self):
        result = jev.rerank("26.17 亚索 Q 冷却", self.hits, api_key="test-key")
        self.assertEqual(result["ordered"][0]["id"], "right")
        self.assertEqual(result["choice_id"], "right")
        self.assertEqual(result["scores"]["right"], 0.81)
        self.assertEqual(result["cost_usd"], 0.000038)

    def test_rerank_asks_one_question_per_candidate(self):
        jev.rerank("26.17 亚索 Q 冷却", self.hits, api_key="test-key")
        _, questions = self.calls[0]
        self.assertIn("pick", questions)
        self.assertEqual([key for key in questions if key.startswith("rel_")], ["rel_i1", "rel_i2", "rel_i3"])

    def test_rerank_without_a_key_returns_none(self):
        original = jev.load_api_key
        jev.load_api_key = lambda: ""
        try:
            self.assertIsNone(jev.rerank("问题", self.hits))
        finally:
            jev.load_api_key = original

    def test_rerank_with_a_single_hit_is_not_called(self):
        self.assertIsNone(jev.rerank("问题", [], api_key="test-key"))


class ConfidenceGateTests(unittest.TestCase):
    """A near-tie must keep the retrieval order; only a clear winner may reorder."""

    hits = [
        {"id": "first", "text": "26.15 锐雯 R 额外攻击力收益 25% → 20%", "rank": 1},
        {"id": "second", "text": "26.15 锐雯 R 额外攻击力收益 最小60%/最大180% → 最小55%/最大165%", "rank": 2},
    ]

    def run_with_scores(self, scores):
        answers = {"pick": {"choice": "i1", "confidence": 0.8, "probabilities": {"i1": 0.8, "i2": 0.2}}}
        for index, (label, value) in enumerate(zip(("i1", "i2"), scores), start=1):
            answers[f"rel_{label}"] = {"noul": value}
        original = jev.ask
        jev.ask = lambda state, questions, **kwargs: {
            "answers": answers,
            "usage": {"input_tokens": 500},
            "model": "jev-test",
            "latency_ms": 100,
            "cost_usd": 0.00002,
        }
        try:
            return jev.rerank("26.15 锐雯 R 额外攻击力收益", self.hits, api_key="test-key")
        finally:
            jev.ask = original

    def test_near_tie_keeps_the_retrieval_order(self):
        result = self.run_with_scores([0.34, 0.39])
        self.assertFalse(result["decisive"])
        self.assertEqual([row["id"] for row in result["ordered"]], ["first", "second"])
        self.assertAlmostEqual(result["gap"], 0.05, places=2)

    def test_clear_winner_reorders(self):
        result = self.run_with_scores([0.04, 0.78])
        self.assertTrue(result["decisive"])
        self.assertEqual([row["id"] for row in result["ordered"]], ["second", "first"])


class DegradationTests(unittest.TestCase):
    def test_ask_returns_none_on_transport_failure(self):
        original = jev.load_api_key
        jev.load_api_key = lambda: "test-key"

        def boom(*args, **kwargs):
            raise OSError("network down")

        original_urlopen = jev.urllib.request.urlopen
        jev.urllib.request.urlopen = boom
        try:
            self.assertIsNone(jev.ask("state", {"q": {"type": "noul", "instructions": "?"}}, retries=0))
        finally:
            jev.urllib.request.urlopen = original_urlopen
            jev.load_api_key = original

    def test_available_without_key_is_false(self):
        original = jev.load_api_key
        jev.load_api_key = lambda: ""
        try:
            self.assertFalse(jev.available())
        finally:
            jev.load_api_key = original


if __name__ == "__main__":
    unittest.main()
