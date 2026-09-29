"""Report-scope tests for the offline evaluator."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_patch import scope_of  # noqa: E402


class ReportScopeTests(unittest.TestCase):
    def test_blind_cases_are_not_labelled_same_source(self):
        scope, note = scope_of([
            Path("blind_cases.json"),
            Path("blind_cases_2.json"),
        ])
        self.assertEqual(scope, "blind")
        self.assertIn("禁止读语料", note)
        self.assertNotIn("非盲测", note)

    def test_same_source_cases_keep_the_regression_warning(self):
        scope, note = scope_of([Path("eval_cases.json")])
        self.assertEqual(scope, "same")
        self.assertIn("非盲测、无留出集", note)

    def test_mixed_case_files_are_labelled_mixed(self):
        scope, note = scope_of([
            Path("blind_cases.json"),
            Path("eval_cases.json"),
        ])
        self.assertEqual(scope, "mixed")
        self.assertIn("混合", note)
