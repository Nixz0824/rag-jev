"""Patch-engine tests: parsing, mode preference, deterministic rendering."""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import patch_engine  # noqa: E402
from patch_engine import PatchEngine, PatchRetriever, field_keys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_retrieval import fake_embed  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"


def build(use_jev: bool = False) -> PatchEngine:
    retriever = PatchRetriever(
        corpus_path=CASES / "corpus.json",
        embed_fn=fake_embed,
        alias_path=CASES / "aliases.json",
        patch_map_path=CASES / "patch_map.json",
        thresholds_path=CASES / "missing-thresholds.json",
    )
    retriever.matrix = np.stack([fake_embed(row["text"]) for row in retriever.chunks])
    return PatchEngine(retriever, use_jev=use_jev)


def ask(engine: PatchEngine, text: str, session: dict | None = None) -> dict:
    session = session or engine.new("s-test")
    session = engine.chat(session, text)
    return session


def answer_text(session: dict) -> str:
    return "\n".join(message["text"] for message in session["messages"] if message["role"] == "assistant")


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.engine = build()

    def test_ddragon_number_maps_to_announcement_number(self):
        self.assertEqual(self.engine.canonical_patch("16.17.1"), "26.17")
        self.assertEqual(self.engine.canonical_patch("26.17"), "26.17")
        self.assertIsNone(self.engine.canonical_patch("26.99"))

    def test_default_version_is_latest(self):
        query = self.engine.parse("亚索改了什么")
        self.assertEqual(query["patches"], ["26.17"])
        self.assertTrue(query["defaulted"])

    def test_last_patch_means_the_previous_one(self):
        query = self.engine.parse("上个版本亚索改了什么")
        self.assertEqual(query["patches"], ["26.16"])
        self.assertFalse(query["defaulted"])

    def test_slang_and_fields(self):
        query = self.engine.parse("26.17 压缩的 q 冷却怎么变")
        self.assertEqual(query["subject"], "亚索")
        self.assertEqual(query["ability"], "Q")
        self.assertIn("cooldown", query["field_keys"])

    def test_mode_detection(self):
        self.assertEqual(self.engine.parse("经典模式亚索改了什么")["mode"], "classic")
        self.assertIsNone(self.engine.parse("亚索改了什么")["mode"])

    def test_field_keys_helper(self):
        self.assertEqual(field_keys("冷却和攻击速度"), ["cooldown", "attack_speed"])
        self.assertEqual(field_keys("价格"), ["price"])


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.engine = build()

    def test_numbers_come_from_the_corpus(self):
        session = ask(self.engine, "26.17 亚索 Q 冷却改成多少了")
        text = answer_text(session)
        self.assertIn("3.5", text)
        self.assertIn("4", text)
        self.assertIn("26.17", text)
        self.assertEqual(session["status"], "verify")

    def test_rift_change_wins_over_classic_mode(self):
        session = ask(self.engine, "26.17 亚索的攻击力改了吗")
        evidence = session["evidence"]
        self.assertTrue(evidence)
        self.assertNotEqual(evidence[0]["mode"], "classic")

    def test_classic_mode_can_be_requested(self):
        session = ask(self.engine, "26.17 经典模式亚索改了什么")
        self.assertTrue(session["evidence"])
        self.assertTrue(all(row["mode"] == "classic" for row in session["evidence"]))

    def test_overview_lists_changes_without_narrative(self):
        session = ask(self.engine, "26.17 有哪些英雄被削弱")
        text = answer_text(session)
        self.assertIn("劫", text)
        self.assertNotIn("团队希望暴击流重新可用", text)

    def test_unknown_subject_asks_instead_of_dumping_the_patch(self):
        session = ask(self.engine, "26.17 阿狸改了什么")
        self.assertEqual(session["status"], "clarifying")
        text = answer_text(session)
        self.assertIn("阿狸", text)
        self.assertIn("有哪些改动", text)

    def test_out_of_range_patch_abstains(self):
        session = ask(self.engine, "26.99 亚索改了什么")
        self.assertEqual(session["status"], "abstained")
        self.assertIn("26.99", answer_text(session))

    def test_skins_are_out_of_scope(self):
        session = ask(self.engine, "26.17 出了哪些皮肤")
        self.assertEqual(session["status"], "abstained")

    def test_answer_always_carries_a_source(self):
        session = ask(self.engine, "26.17 岚切怎么调整的")
        text = answer_text(session)
        self.assertIn("国服 26.17 版本更新公告", text)

    def test_jev_is_off_by_default_in_tests(self):
        session = ask(self.engine, "26.17 亚索 Q 冷却")
        self.assertFalse(any(trace["tool"] == "Jev 重排" for trace in session["trace"]))


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.engine = build()
        self.engine.feedback_file = Path(__file__).resolve().parent / "cases" / "feedback-test.jsonl"

    def tearDown(self):
        self.engine.feedback_file.unlink(missing_ok=True)

    def test_feedback_requires_an_answer_waiting_for_review(self):
        session = self.engine.new("s-feedback")
        with self.assertRaises(ValueError):
            self.engine.feedback(session, "accurate")

    def test_feedback_is_recorded_and_closes_the_answer(self):
        session = ask(self.engine, "26.17 亚索 Q 冷却")
        self.engine.feedback(session, "accurate")
        self.assertEqual(session["status"], "done")
        self.assertTrue(self.engine.feedback_file.exists())

    def test_invalid_feedback_type_is_rejected(self):
        session = ask(self.engine, "26.17 亚索 Q 冷却")
        with self.assertRaises(ValueError):
            self.engine.feedback(session, "great")


if __name__ == "__main__":
    unittest.main()
