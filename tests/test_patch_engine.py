"""Patch-engine tests: parsing, mode preference, deterministic rendering."""

import json
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

    def test_colloquial_ability_and_field_words(self):
        query = self.engine.parse("压缩的大招几秒")
        self.assertEqual(query["subject"], "亚索")
        self.assertEqual(query["ability"], "R")
        self.assertIn("cooldown", query["field_keys"])

    def test_latin_name_next_to_a_version_still_resolves(self):
        query = self.engine.parse("Yasuo 26.17 改了什么")
        self.assertEqual(query["subject"], "亚索")
        self.assertEqual(query["patches"], ["26.17"])

    def test_field_keys_helper(self):
        self.assertEqual(field_keys("冷却和攻击速度"), ["cooldown", "attack_speed"])
        self.assertEqual(field_keys("价格"), ["price"])

    def test_exact_version_spelling_wins_over_offset(self):
        """2025 used both 15.x and 25.x; an exact corpus match must not be shifted."""
        self.engine.r.supported = ["15.13", "26.16", "26.17"]
        self.assertEqual(self.engine.canonical_patch("15.13"), "15.13")
        self.assertEqual(self.engine.canonical_patch("25.13"), "15.13")
        self.assertEqual(self.engine.canonical_patch("16.17"), "26.17")
        self.assertIsNone(self.engine.canonical_patch("31.7"))


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


class VersionContextTests(unittest.TestCase):
    """Behaviours the blind set exposed: selectors, past tense, latest-change fallback."""

    def setUp(self):
        self.engine = build()

    def test_past_tense_passive_is_not_an_ability(self):
        query = self.engine.parse("哪些英雄在这一版被动过")
        self.assertIsNone(query["ability"])
        self.assertTrue(query["overview"])

    def test_ui_selection_applies_only_without_an_explicit_version(self):
        session = self.engine.new("s-context")
        session = self.engine.chat(session, "亚索改了什么", selected_patch="26.16")
        self.assertEqual(session["query"]["patches"], ["26.16"])
        session = self.engine.chat(self.engine.new("s-context2"), "26.17 亚索改了什么", selected_patch="26.16")
        self.assertEqual(session["query"]["patches"], ["26.17"])

    def test_missing_stat_falls_back_to_the_latest_change(self):
        session = ask(self.engine, "亚索生命值现在多少")
        text = answer_text(session)
        self.assertEqual(session["status"], "verify")
        self.assertIn("最近一次改动", text)
        self.assertIn("26.16", text)
        self.assertIn("110", text)
        self.assertTrue(any(step["tool"] == "版本回退" for step in session["trace"]))

    def test_explicit_version_still_abstains_instead_of_falling_back(self):
        session = ask(self.engine, "26.17 亚索生命值")
        self.assertEqual(session["status"], "abstained")

    def test_slang_nickname_resolves(self):
        self.assertEqual(self.engine.parse("压缩改了什么")["subject"], "亚索")

    def test_shipped_alias_table_contains_common_nicknames(self):
        aliases = json.loads((ROOT / "data" / "patch" / "aliases.json").read_text(encoding="utf-8"))
        for nickname, subject in (("牛头", "阿利斯塔"), ("男枪", "格雷福斯"), ("女警", "凯特琳"), ("飞机", "库奇"),
                                  ("船长", "普朗克"), ("鸟皇", "阿兹尔"), ("熊", "沃利贝尔")):
            self.assertIn(nickname, aliases.get(subject, {}).get("aliases", []), f"{nickname} → {subject}")

    def test_shipped_alias_table_contains_item_nicknames(self):
        """Items are asked by nickname far more often than by official name."""
        aliases = json.loads((ROOT / "data" / "patch" / "aliases.json").read_text(encoding="utf-8"))
        for nickname, subject in (("电刀", "斯塔缇克电刃"), ("无尽", "无尽之刃"), ("帽子", "灭世者的死亡之帽"),
                                  ("冰杖", "瑞莱的冰晶节杖"), ("反甲", "荆棘之甲"), ("黑切", "黑色切割者"),
                                  ("破败", "破败王者之刃"), ("羊刀", "鬼索的狂暴之刃")):
            self.assertIn(nickname, aliases.get(subject, {}).get("aliases", []), f"{nickname} → {subject}")

    def test_ability_name_resolves_to_a_letter(self):
        """Players ask by skill name far more often than by letter."""
        query = self.engine.parse("亚索的斩钢闪改了吗")
        self.assertEqual(query["subject"], "亚索")
        self.assertEqual(query["ability"], "Q")
        self.assertEqual(query.get("ability_from_name"), "斩钢闪")

    def test_passive_name_resolves_to_passive(self):
        query = self.engine.parse("亚索的浪客之道改了吗")
        self.assertEqual(query["ability"], "被动")

    def test_shipped_alias_table_covers_renamed_and_codenamed_items(self):
        aliases = json.loads((ROOT / "data" / "patch" / "aliases.json").read_text(encoding="utf-8"))
        for subject in ("卢登的配枪", "C44", "不朽之路"):
            self.assertIn(subject, aliases, f"{subject} 应从语料补入别名表")
            self.assertEqual(aliases[subject]["kind"], "item")

    def test_unresolved_name_hint_mentions_items(self):
        session = ask(self.engine, "26.17 电刀的攻速")
        self.assertEqual(session["status"], "clarifying")
        self.assertIn("没认出", answer_text(session))

    def test_earlier_subject_wins_over_a_shorter_item_inside_an_ability_name(self):
        """'瑞兹 R 被动过载涌动伤害' must resolve to 瑞兹, not to the item 过载."""
        engine = self.engine
        engine.r.aliases = sorted(
            {("瑞兹", "瑞兹", "champion"), ("过载", "过载", "item")}, key=lambda item: len(item[0]), reverse=True
        )
        self.assertEqual(engine.r.resolve_subject("瑞兹 R 被动过载涌动伤害"), ("瑞兹", "champion"))


class AggregateTests(unittest.TestCase):
    """Cross-version aggregation: one subject over a span of patches."""

    def setUp(self):
        self.engine = build()

    def test_range_question_returns_a_timeline(self):
        session = ask(self.engine, "亚索从 26.16 到 26.17 一共改了几次")
        text = answer_text(session)
        self.assertEqual(session["status"], "verify")
        self.assertIn("共被改动", text)
        self.assertIn("26.16", text)
        self.assertIn("26.17", text)
        self.assertTrue(any(step["tool"] == "跨版本聚合" for step in session["trace"]))

    def test_aggregate_question_without_a_range_covers_the_whole_corpus(self):
        session = ask(self.engine, "亚索历次改动有哪些")
        text = answer_text(session)
        self.assertIn("26.16", text)
        self.assertIn("方向：加强", text)

    def test_direction_narrows_the_timeline(self):
        session = ask(self.engine, "薇恩历次被削弱的记录")
        text = answer_text(session)
        self.assertIn("削弱", text)
        self.assertIn("真实伤害", text)
        self.assertNotIn("生命值", text)

    def test_missing_direction_is_reported_instead_of_showing_everything(self):
        session = ask(self.engine, "26.17 亚索历次被削弱的记录")
        text = answer_text(session)
        self.assertIn("没有削弱记录", text)
        self.assertIn("全部改动", text)

    def test_unknown_subject_still_asks_for_a_name(self):
        session = ask(self.engine, "阿狸从 26.16 到 26.17 一共改了几次")
        self.assertEqual(session["status"], "clarifying")
        self.assertIn("阿狸", answer_text(session))


class ClaimTests(unittest.TestCase):
    """A question that asserts the wrong direction must be corrected, not confirmed."""

    def setUp(self):
        self.engine = build()

    def test_false_claim_is_corrected(self):
        session = ask(self.engine, "26.17 亚索被削了吗")
        text = answer_text(session)
        self.assertIn("没有被削弱", text)
        self.assertIn("加强", text)

    def test_mixed_directions_are_both_reported(self):
        session = ask(self.engine, "26.17 薇恩被削弱了吗")
        text = answer_text(session)
        self.assertIn("既有削弱也有加强", text)
        self.assertIn("真实伤害", text)
        self.assertIn("生命值", text)

    def test_listing_question_still_filters_by_direction(self):
        session = ask(self.engine, "26.17 有哪些英雄被削弱")
        text = answer_text(session)
        self.assertIn("薇恩", text)
        self.assertNotIn("亚索", text)

    def test_claim_check_is_traced(self):
        session = ask(self.engine, "26.17 亚索被削了吗")
        self.assertTrue(any(step["tool"] == "核对断言" for step in session["trace"]))

    def test_absent_field_abstains_and_lists_what_exists(self):
        session = ask(self.engine, "26.17 亚索 Q 射程是多少")
        self.assertEqual(session["status"], "abstained")
        text = answer_text(session)
        self.assertIn("没有", text)
        self.assertIn("射程/范围", text)
        self.assertIn("冷却时间", text)

    def test_other_server_question_is_out_of_scope(self):
        session = ask(self.engine, "美服 26.17 亚索数值和国服一样吗")
        self.assertEqual(session["status"], "abstained")
        self.assertIn("国服", answer_text(session))


class SelfCheckTests(unittest.TestCase):
    """The answer self-check must be visible when it fires and absent when it does not."""

    def setUp(self):
        self.engine = build()
        self.engine.use_jev = True
        self.engine.self_check = True
        self.original = patch_engine.jev.judge_many

    def tearDown(self):
        patch_engine.jev.judge_many = self.original

    @staticmethod
    def verdict(minimum: float):
        def fake(pairs, **kwargs):
            return {
                "scores": [minimum] * len(pairs),
                "min": minimum,
                "mean": minimum,
                "usage": {"input_tokens": 300},
                "latency_ms": 90,
                "cost_usd": 0.000013,
                "model": "jev-test",
            }

        return fake

    def test_weak_evidence_adds_a_caution(self):
        patch_engine.jev.judge_many = self.verdict(0.2)
        session = ask(self.engine, "26.17 亚索 Q 冷却")
        self.assertIn("一致性偏低", answer_text(session))
        self.assertEqual(session["self_check"]["min"], 0.2)
        self.assertTrue(any(step["tool"] == "回答自检" for step in session["trace"]))

    def test_strong_evidence_stays_silent(self):
        patch_engine.jev.judge_many = self.verdict(0.93)
        session = ask(self.engine, "26.17 亚索 Q 冷却")
        self.assertNotIn("一致性偏低", answer_text(session))
        self.assertEqual(session["self_check"]["min"], 0.93)

    def test_self_check_is_off_by_default(self):
        self.engine.self_check = False
        calls = []
        patch_engine.jev.judge_many = lambda pairs, **kwargs: calls.append(pairs) or self.verdict(0.9)(pairs)
        ask(self.engine, "26.17 亚索 Q 冷却")
        self.assertEqual(calls, [])


class CompareTests(unittest.TestCase):
    """Version comparison is pure data, so it can be tested without the model."""

    def setUp(self):
        retriever = PatchRetriever(
            corpus_path=CASES / "corpus.json",
            embed_fn=fake_embed,
            alias_path=CASES / "aliases.json",
            patch_map_path=CASES / "patch_map.json",
            thresholds_path=CASES / "missing-thresholds.json",
        )
        self.rows = retriever.chunks

    def test_changed_added_and_removed_are_distinguished(self):
        rows_a = [row for row in self.rows if row["patch"] == "26.16"]
        rows_b = [row for row in self.rows if row["patch"] == "26.17"]
        groups = patch_engine.compare_versions(rows_a, rows_b)
        by_subject = {group["subject"]: group for group in groups}

        yasuo_rows = {row["field"]: row for row in by_subject["亚索"]["rows"]}
        self.assertEqual(yasuo_rows["伤害"]["status"], "changed")
        self.assertEqual((yasuo_rows["伤害"]["a"], yasuo_rows["伤害"]["b"]), ("20", "25"))
        self.assertEqual(yasuo_rows["冷却时间"]["status"], "added")
        self.assertEqual(yasuo_rows["攻击力"]["status"], "added")
        # 薇恩 is new in 26.17, so both rows are additions rather than changes.
        self.assertEqual(by_subject["薇恩"]["changed"], 2)
        self.assertTrue(all(row["status"] == "added" for row in by_subject["薇恩"]["rows"]))

    def test_narratives_are_ignored(self):
        rows_a = [row for row in self.rows if row["patch"] == "26.16"]
        rows_b = [row for row in self.rows if row["patch"] == "26.17"]
        for group in patch_engine.compare_versions(rows_a, rows_b):
            for row in group["rows"]:
                self.assertNotEqual(row["field"], "说明")

    def test_identical_patches_have_no_changes(self):
        rows_b = [row for row in self.rows if row["patch"] == "26.17"]
        groups = patch_engine.compare_versions(rows_b, rows_b)
        self.assertTrue(all(group["changed"] == 0 for group in groups))


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
