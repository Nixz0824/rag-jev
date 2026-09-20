"""Unit tests for the shared patch-note schema."""

import unittest

from patch_schema import (
    CHANGE_LINE_RE,
    ARROW_RE,
    chunk_id,
    clean,
    detect_mode,
    direction,
    field_key,
    sentence,
    split_subject,
)


class FieldKeyTests(unittest.TestCase):
    def test_common_labels_map_to_keys(self):
        self.assertEqual(field_key("冷却时间"), "cooldown")
        self.assertEqual(field_key("每秒消耗魔力"), "cost")
        self.assertEqual(field_key("成长攻击力"), "attack_damage")
        self.assertEqual(field_key("基础生命值"), "health")
        self.assertEqual(field_key("总花费"), "price")
        self.assertEqual(field_key("技能急速"), "ability_haste")

    def test_specific_pattern_wins(self):
        self.assertEqual(field_key("生命偷取"), "lifesteal")
        self.assertEqual(field_key("生命回复"), "health_regen")
        self.assertIsNone(field_key("某个没人认识的东西"))


class DirectionTests(unittest.TestCase):
    def test_first_number_decides(self):
        self.assertEqual(direction("4", "3.5"), "nerf")
        self.assertEqual(direction("20", "25"), "buff")
        self.assertEqual(direction("-10%", "-5%"), "buff")
        self.assertEqual(direction("50%", "50%"), "adjust")
        self.assertEqual(direction("很久", "更久"), "adjust")


class CleanTests(unittest.TestCase):
    def test_bullets_and_entities_are_stripped(self):
        self.assertEqual(clean("●伤害：20"), "伤害：20")
        self.assertEqual(clean("&nbsp; 冷却时间 ：4"), "冷却时间 ：4")
        self.assertEqual(clean("· 说明"), "说明")


class ChangeLineTests(unittest.TestCase):
    def test_both_arrow_styles_parse(self):
        double = CHANGE_LINE_RE.match("冷却时间：4 ⇒ 3.5")
        thin = CHANGE_LINE_RE.match("伤害：70 → 65")
        self.assertEqual((double.group("old"), double.group("new")), ("4", "3.5"))
        self.assertEqual((thin.group("old"), thin.group("new")), ("70", "65"))
        self.assertIsNone(CHANGE_LINE_RE.match("现在会在每次命中时叠加2层，而不是1层"))
        self.assertTrue(ARROW_RE.search("a ⇒ b"))
        self.assertTrue(ARROW_RE.search("a → b"))
        self.assertFalse(ARROW_RE.search("没有箭头"))


class SubjectTests(unittest.TestCase):
    def test_title_and_name_split(self):
        self.assertEqual(split_subject("疾风剑豪 亚索"), ("疾风剑豪", "亚索"))
        self.assertEqual(split_subject("岚切"), ("", "岚切"))


class IdTests(unittest.TestCase):
    def test_ids_are_stable_and_distinct(self):
        first = chunk_id("26.17", "rift", "亚索", "Q", "冷却时间", "4", "3.5")
        second = chunk_id("26.17", "rift", "亚索", "Q", "冷却时间", "4", "3.5")
        other = chunk_id("26.17", "rift", "亚索", "Q", "冷却时间", "4", "4.5")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)


class SentenceTests(unittest.TestCase):
    def test_verb_follows_direction(self):
        row = {"patch": "26.17", "subject": "亚索", "ability": "", "field": "攻击力", "old_value": "60", "new_value": "62", "direction": "buff"}
        self.assertIn("提升至", sentence(row))
        row["direction"] = "nerf"
        self.assertIn("降低至", sentence(row))
        row["old_value"] = ""
        self.assertIn("为", sentence(row))


class ModeTests(unittest.TestCase):
    def test_mode_keywords(self):
        self.assertEqual(detect_mode("经典模式亚索改了什么"), "classic")
        self.assertEqual(detect_mode("海克斯大乱斗"), "aram")
        self.assertEqual(detect_mode("斗魂竞技场"), "arena")
        self.assertIsNone(detect_mode("亚索改了什么"))


if __name__ == "__main__":
    unittest.main()
