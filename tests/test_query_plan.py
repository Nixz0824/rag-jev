"""Query-plan and taxonomy contracts: provenance, closure, and serialisation.

These tests exist because the whole semantic-routing design rests on two promises that are
easy to break silently while refactoring:

1. a slot the deterministic parser resolved is **closed** — no model may re-ask it;
2. every slot records *how* it was decided, so the UI and the evaluations can tell a rule
   result from a model result without re-deriving it.

They also pin the taxonomy's declared shape (single home per field key, real corpus
vocabulary) so the tree cannot drift away from ``patch_schema.FIELDS``.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import taxonomy  # noqa: E402
from patch_schema import FIELDS  # noqa: E402
from query_plan import (  # noqa: E402
    PATCH_SYSTEM_DEFAULT,
    SOURCE_ALIAS,
    SOURCE_DETERMINISTIC,
    SOURCE_NONE,
    SOURCE_SEMANTIC,
    STATUS_AMBIGUOUS,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    QueryPlan,
    RoutePath,
    Slot,
)


class SlotTests(unittest.TestCase):
    def test_closed_only_for_rule_and_alias_results(self):
        self.assertTrue(Slot("ability", "W", SOURCE_DETERMINISTIC, STATUS_RESOLVED).closed)
        self.assertTrue(Slot("subject", "亚索", SOURCE_ALIAS, STATUS_RESOLVED).closed)
        # A model result is never "closed": it may still be branched on.
        self.assertFalse(Slot("ability", "W", SOURCE_SEMANTIC, STATUS_RESOLVED, 0.9).closed)
        self.assertFalse(Slot("ability", None, SOURCE_NONE, STATUS_UNRESOLVED).closed)
        self.assertFalse(Slot("field", "", SOURCE_DETERMINISTIC, STATUS_RESOLVED).closed)
        self.assertFalse(Slot("field", [], SOURCE_DETERMINISTIC, STATUS_RESOLVED).closed)

    def test_to_json_omits_absent_confidence_and_candidates(self):
        payload = Slot("subject", "亚索", SOURCE_ALIAS, STATUS_RESOLVED, why="别名命中").to_json()
        self.assertEqual(payload["value"], "亚索")
        self.assertEqual(payload["source"], "alias")
        self.assertNotIn("confidence", payload)
        self.assertNotIn("candidates", payload)

    def test_to_json_keeps_confidence_and_candidates_when_present(self):
        slot = Slot("ability", "W", SOURCE_SEMANTIC, STATUS_RESOLVED, 0.91234,
                    candidates=[{"value": "W", "probability": 0.91}])
        payload = slot.to_json()
        self.assertEqual(payload["confidence"], 0.9123)
        self.assertEqual(payload["candidates"][0]["value"], "W")


class PlanTests(unittest.TestCase):
    def test_set_slot_infers_status_from_the_value(self):
        plan = QueryPlan(question="q")
        self.assertEqual(plan.set_slot("subject", "亚索", SOURCE_ALIAS).status, STATUS_RESOLVED)
        self.assertEqual(plan.set_slot("ability", None, SOURCE_NONE).status, STATUS_UNRESOLVED)
        self.assertEqual(plan.set_slot("field_keys", [], SOURCE_NONE).status, STATUS_UNRESOLVED)

    def test_unresolved_slots_ignore_closed_and_exclude_subject(self):
        plan = QueryPlan(question="q")
        plan.set_slot("patches", ["26.17"], SOURCE_DETERMINISTIC)
        plan.set_slot("subject", "亚索", SOURCE_ALIAS)
        plan.set_slot("ability", None, SOURCE_NONE, STATUS_UNRESOLVED)
        plan.set_slot("field", None, SOURCE_NONE, STATUS_AMBIGUOUS)
        # A slot that was never set counts as unresolved too: not deciding is not deciding.
        plan.set_slot("type", "champion", SOURCE_ALIAS)
        plan.set_slot("intent", "single_fact", SOURCE_DETERMINISTIC)
        self.assertEqual(sorted(plan.refresh_unresolved()), ["ability", "field", "mode"])
        # subject is deliberately not fallback-eligible: entities go through a shortlist.
        self.assertNotIn("subject", plan.unresolved_slots)

    def test_a_never_set_slot_counts_as_unresolved(self):
        plan = QueryPlan(question="q")
        self.assertEqual(sorted(plan.refresh_unresolved()),
                         ["ability", "field", "intent", "mode", "type"])

    def test_a_semantically_resolved_slot_leaves_the_unresolved_list(self):
        plan = QueryPlan(question="q")
        plan.set_slot("ability", "W", SOURCE_SEMANTIC, STATUS_RESOLVED, 0.51)
        self.assertNotIn("ability", plan.refresh_unresolved())

    def test_resolved_deterministically_guards_the_router(self):
        plan = QueryPlan(question="q")
        plan.set_slot("ability", "W", SOURCE_DETERMINISTIC, why="显式技能字母 W")
        plan.set_slot("mode", "aram", SOURCE_SEMANTIC, confidence=0.4)
        self.assertTrue(plan.resolved_deterministically("ability"))
        self.assertFalse(plan.resolved_deterministically("mode"))

    def test_value_treats_empty_containers_as_absent(self):
        plan = QueryPlan(question="q")
        plan.set_slot("field_keys", [], SOURCE_NONE)
        plan.set_slot("patches", ["26.17"], SOURCE_DETERMINISTIC)
        self.assertIsNone(plan.value("field_keys"))
        self.assertEqual(plan.value("patches"), ["26.17"])
        self.assertEqual(plan.value("missing", "fallback"), "fallback")

    def test_to_json_is_stable_and_complete(self):
        plan = QueryPlan(question="26.17 薇恩 W 真实伤害是多少")
        plan.patches = ["26.17"]
        plan.set_slot("patches", ["26.17"], SOURCE_DETERMINISTIC, why="正则命中显式版本")
        plan.set_slot("subject", "薇恩", SOURCE_ALIAS, why="别名命中「vn」")
        plan.set_slot("ability", "W", SOURCE_DETERMINISTIC, why="显式技能字母 W")
        plan.set_slot("field", "damage", SOURCE_DETERMINISTIC, why="字段规则命中「伤害」")
        plan.set_slot("mode", "rift", SOURCE_DETERMINISTIC, why="模式关键词命中")
        plan.set_slot("type", "champion", SOURCE_ALIAS, why="由别名表推断类别")
        plan.set_slot("intent", "single_fact", SOURCE_DETERMINISTIC, why="有对象且没有总览说法")
        plan.refresh_unresolved()
        payload = plan.to_json()
        self.assertEqual(payload["patches"], ["26.17"])
        self.assertEqual(payload["slots"]["ability"]["source"], "deterministic")
        self.assertEqual(payload["slots"]["subject"]["why"], "别名命中「vn」")
        # Every slot in a fully-explicit question is decided, so nothing is left to ask.
        self.assertEqual(payload["unresolved_slots"], [])
        self.assertEqual(payload["routing"]["mode"], "off")
        self.assertFalse(payload["routing"]["beam_used"])
        self.assertIsNone(payload["routing"]["confidence"])
        # Round-trips through JSON without a custom encoder.
        import json

        self.assertEqual(json.loads(json.dumps(payload, ensure_ascii=False))["question"], plan.question)

    def test_to_json_serialises_a_beam_and_paths(self):
        plan = QueryPlan(question="薇恩的技能伤害改过吗")
        plan.routing_mode = "active"
        plan.beam_used = True
        plan.routing_confidence = 0.51
        plan.route_paths = [
            RoutePath(nodes=[{"node": "ability", "value": "W", "probability": 0.51}], score=0.51,
                      source=SOURCE_SEMANTIC, where={"subject": "薇恩", "ability": "W"},
                      candidate_count=4, contributed=True,
                      detail="首选读法：ability=W"),
            RoutePath(nodes=[{"node": "ability", "value": "被动", "probability": 0.47}], score=0.47,
                      source=SOURCE_SEMANTIC, where={"subject": "薇恩", "ability": "被动"},
                      candidate_count=2, detail="竞争读法：ability=被动"),
        ]
        payload = plan.to_json()
        self.assertTrue(payload["routing"]["beam_used"])
        self.assertEqual(payload["routing"]["confidence"], 0.51)
        self.assertEqual(len(payload["routing"]["paths"]), 2)
        self.assertEqual(payload["routing"]["paths"][0]["label"], "ability=W")
        self.assertEqual(payload["routing"]["paths"][0]["candidate_count"], 4)
        self.assertTrue(payload["routing"]["paths"][0]["contributed"])
        self.assertFalse(payload["routing"]["paths"][1]["contributed"])

    def test_path_label_is_readable_without_nodes(self):
        self.assertEqual(RoutePath().label, "unfiltered")

    def test_patch_source_default_is_system_not_explicit(self):
        plan = QueryPlan(question="q")
        self.assertEqual(plan.patch_source, PATCH_SYSTEM_DEFAULT)


class TaxonomyTests(unittest.TestCase):
    def test_every_schema_field_has_at_most_one_family(self):
        seen: dict[str, str] = {}
        for family, keys in taxonomy.FIELD_FAMILIES.items():
            for key in keys:
                self.assertNotIn(key, seen, f"{key} appears in {seen.get(key)} and {family}")
                seen[key] = family

    def test_every_field_family_key_exists_in_the_schema(self):
        schema = {key for key, _ in FIELDS}
        for family, keys in taxonomy.FIELD_FAMILIES.items():
            for key in keys:
                self.assertIn(key, schema, f"{family}.{key} is not a schema field key")

    def test_unclassified_fields_are_exactly_the_remainder(self):
        schema = {key for key, _ in FIELDS}
        classified = set(taxonomy.FIELD_TO_FAMILY)
        self.assertEqual(set(taxonomy.UNCLASSIFIED_FIELDS), schema - classified)

    def test_family_of_is_total(self):
        for key, _ in FIELDS:
            self.assertTrue(taxonomy.family_of(key))
        self.assertEqual(taxonomy.family_of("no_such_field"), "unknown")
        self.assertEqual(taxonomy.family_of(""), "unknown")

    def test_families_of_preserves_order_and_deduplicates(self):
        self.assertEqual(taxonomy.families_of(["damage", "attack_damage", "cooldown"]), ["damage", "tempo"])
        self.assertEqual(taxonomy.families_of([]), [])

    def test_ability_and_mode_vocabulary_covers_the_corpus(self):
        # The corpus stores 被动 (not "P") and "" for base stats; the taxonomy must use the
        # corpus's own spelling or the ability filter would never match.
        self.assertIn("被动", taxonomy.ABILITIES)
        self.assertIn("base", taxonomy.ABILITIES)
        self.assertNotIn("P", taxonomy.ABILITIES)
        self.assertEqual(set(taxonomy.MODES), {"rift", "aram", "arena", "classic", "unspecified"})

    def test_field_choices_are_named_in_human_terms(self):
        choices = taxonomy.field_choices("damage")
        self.assertIn("damage", choices)
        self.assertIn("伤害", choices["damage"])
        self.assertEqual(taxonomy.field_choices("unknown"), {})

    def test_family_choices_put_the_hinted_family_first(self):
        choices = list(taxonomy.family_choices(["cooldown"]))
        self.assertEqual(choices[0], "tempo")
        self.assertNotIn("unknown", choices[:1])

    def test_route_nodes_match_the_plan_slot_names(self):
        self.assertEqual(taxonomy.ROUTE_NODES, ("intent", "mode", "type", "ability", "field"))
        for slot, node in taxonomy.SLOT_TO_NODE.items():
            self.assertIn(node, taxonomy.ROUTE_NODES)
            self.assertIn(slot, ("intent", "mode", "type", "ability", "field"))


if __name__ == "__main__":
    unittest.main()
