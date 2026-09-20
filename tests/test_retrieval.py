"""Retrieval tests: metadata filtering must happen before ranking."""

import hashlib
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import Retriever, redact, tokens  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
DIM = 64


def fake_embed(text: str) -> np.ndarray:
    """Deterministic pseudo-embedding: hashed character trigrams, unit length."""
    vector = np.zeros(DIM, dtype=np.float32)
    lowered = text.lower()
    for index in range(len(lowered) - 2):
        digest = hashlib.sha1(lowered[index : index + 3].encode()).digest()
        vector[digest[0] % DIM] += 1.0
    return vector / max(float(np.linalg.norm(vector)), 1e-10)


def build() -> Retriever:
    retriever = Retriever(CASES / "corpus.json", "Instruct: test.\nQuery: ", "fake-embed-v1", embed_fn=fake_embed)
    retriever.matrix = np.stack([fake_embed(f"{row['text']} {row.get('keywords', '')}") for row in retriever.chunks])
    return retriever


class TokenTests(unittest.TestCase):
    def test_chinese_bigrams_and_latin_words(self):
        self.assertIn("亚索", tokens("亚索Q冷却"))
        self.assertIn("yasuo", tokens("Yasuo Q"))
        self.assertEqual(tokens("卡"), ["卡"])


class RedactTests(unittest.TestCase):
    def test_credentials_are_hidden(self):
        self.assertIn("[凭据已隐藏]", redact("我的 key 是 sk-abcdefgh12345"))
        self.assertIn("[邮箱已隐藏]", redact("联系 a@b.com"))


class FilterTests(unittest.TestCase):
    def test_patch_filter_excludes_other_versions(self):
        retriever = build()
        hits = retriever.search("亚索 Q 伤害", k=10, where={"patches": ["26.17"], "subject": "亚索"})
        self.assertTrue(hits)
        self.assertTrue(all(hit["patch"] == "26.17" for hit in hits))

    def test_subject_filter_excludes_other_champions(self):
        retriever = build()
        hits = retriever.search("伤害", k=10, where={"subject": "亚索"})
        self.assertTrue(hits)
        self.assertTrue(all(hit["subject"] == "亚索" for hit in hits))

    def test_field_key_is_available_for_filtering(self):
        retriever = build()
        hits = retriever.search("亚索 Q 冷却", k=10, where={"subject": "亚索", "patches": ["26.17"]})
        keys = {hit["field_key"] for hit in hits}
        self.assertIn("cooldown", keys)

    def test_empty_filter_result_is_empty_not_error(self):
        retriever = build()
        self.assertEqual(retriever.search("亚索", k=5, where={"patches": ["99.99"]}), [])

    def test_metadata_filter_runs_before_ranking(self):
        """A perfect lexical match from the wrong patch must not win."""
        retriever = build()
        hits = retriever.search("亚索 Q 伤害 20 25", k=5, where={"patches": ["26.16"]})
        self.assertTrue(all(hit["patch"] == "26.16" for hit in hits))


class ModeTests(unittest.TestCase):
    def test_three_modes_are_ranked_independently(self):
        retriever = build()
        bm25 = retriever.search("亚索", mode="bm25", k=10)
        vector = retriever.search("亚索", mode="vector", k=10)
        hybrid = retriever.search("亚索", mode="hybrid", k=10)
        for hits in (bm25, vector, hybrid):
            self.assertEqual(len(hits), 7)
            self.assertEqual(hits[0]["rank"], 1)
        self.assertIn("bm25", bm25[0])
        self.assertIn("similarity", vector[0])


class GateTests(unittest.TestCase):
    def test_gate_reports_its_thresholds(self):
        retriever = build()
        hits = retriever.search("亚索", k=3)
        verdict = retriever.gate(hits)
        self.assertIn("supported", verdict)
        self.assertEqual(verdict["thresholds"], {"similarity": 0.48, "bm25": 6.0})

    def test_weak_evidence_is_not_supported(self):
        retriever = build()
        verdict = retriever.gate([{"similarity": 0.2, "bm25": 0.1}])
        self.assertFalse(verdict["supported"])

    def test_strong_keyword_hit_is_supported(self):
        retriever = build()
        verdict = retriever.gate([{"similarity": 0.1, "bm25": 9.0}])
        self.assertTrue(verdict["supported"])


if __name__ == "__main__":
    unittest.main()
