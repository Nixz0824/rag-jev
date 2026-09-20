"""Ingestion tests: the parser must not depend on the live website."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_qq  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"


class ParseArticleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.aliases = json.loads((CASES / "aliases.json").read_text(encoding="utf-8"))
        cls.document = (CASES / "article.html").read_text(encoding="utf-8")
        cls.chunks, cls.stats = ingest_qq.parse_article("26.17", cls.document, cls.aliases)

    def find(self, subject, field_key, ability=""):
        return [row for row in self.chunks if row["subject"] == subject and row["field_key"] == field_key and row["ability"] == ability]

    def test_both_arrow_styles_become_changes(self):
        self.assertEqual(self.stats["changes"], 7)
        self.assertEqual(self.find("亚索", "damage", "Q")[0]["new_value"], "25")
        self.assertEqual(self.find("劫", "damage", "Q")[0]["old_value"], "70")
        self.assertEqual(self.find("劫", "damage", "Q")[0]["direction"], "nerf")

    def test_base_stats_do_not_inherit_an_ability(self):
        rows = self.find("亚索", "attack_damage", "")
        self.assertTrue(rows, "base attack damage must stay outside the ability context")
        self.assertEqual(rows[0]["old_value"], "60")

    def test_ability_name_is_kept(self):
        rows = self.find("亚索", "cooldown", "Q")
        self.assertEqual(rows[0]["ability_name"], "斩钢闪")

    def test_items_get_their_own_subject(self):
        rows = self.find("岚切", "attack_speed")
        self.assertEqual(rows[0]["subject_en"], "Stormrazor")

    def test_mode_section_is_tagged(self):
        classic = [row for row in self.chunks if row["mode"] == "classic"]
        self.assertTrue(classic)
        self.assertTrue(all(row["patch"] == "26.17" for row in classic))

    def test_skins_and_footer_never_leak(self):
        blob = " ".join(row["text"] for row in self.chunks)
        self.assertNotIn("测试皮肤", blob)
        self.assertNotIn("其他内容", blob)

    def test_narrative_rows_are_marked(self):
        narrative = [row for row in self.chunks if row["confidence"] == "narrative"]
        self.assertTrue(narrative)
        self.assertTrue(all(row["field_key"] == "narrative" for row in narrative))

    def test_every_change_has_a_sentence_and_an_id(self):
        for row in self.chunks:
            self.assertTrue(row["id"])
            self.assertTrue(row["text"].startswith("26.17"))


class HelperTests(unittest.TestCase):
    def test_resolve_patch_reads_h1_or_intro(self):
        self.assertEqual(ingest_qq.resolve_patch("<h1>26.13版本更新公告</h1>"), "26.13")
        self.assertEqual(ingest_qq.resolve_patch("<p>发布26.12版本，预计停机</p>"), "26.12")
        self.assertIsNone(ingest_qq.resolve_patch("<h1>6月25日凌晨1点停机版本更新公告</h1>"))

    def test_ddragon_mapping(self):
        versions = ["16.18.1", "16.17.1", "16.16.1"]
        self.assertEqual(ingest_qq.ddragon_map("26.17", versions), "16.17.1")
        self.assertEqual(ingest_qq.ddragon_map("26.9", versions), "")

    def test_ddragon_mapping_handles_both_numbering_schemes(self):
        versions = ["16.18.1", "16.17.1", "15.15.1", "15.13.1"]
        self.assertEqual(ingest_qq.ddragon_map("15.13", versions), "15.13.1")
        self.assertEqual(ingest_qq.ddragon_map("25.15", versions), "15.15.1")

    def test_title_patterns(self):
        self.assertTrue(ingest_qq.TITLE_RE.match("26.18版本更新公告"))
        self.assertTrue(ingest_qq.TITLE_RE.match("26.11版本公告-辅助格局一新"))
        self.assertFalse(ingest_qq.TITLE_RE.match("18.2云顶之弈版本更新公告"))
        self.assertTrue(ingest_qq.DATE_TITLE_RE.match("6月25日凌晨1点停机版本更新公告"))


if __name__ == "__main__":
    unittest.main()
