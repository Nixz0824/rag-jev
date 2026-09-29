"""Hierarchical taxonomy for semantic routing, derived from the real corpus.

Everything here is data, not policy: the router in ``routing.py`` decides *when* to
walk this tree, and ``jev.py`` only answers the choice questions the router asks.
Building the tree in its own module keeps ``jev.py`` a transport/primitives file and
keeps the engine free of category tables.

**How the tree was derived (not invented).** Counts below are the real distribution
over the shipped corpus (4995 chunks / 2212 structured rows, 30 patches,
``15.13—26.18``), measured with a throwaway script against
``data/patch/knowledge.json``:

* ``type``      champion 2233 · system 1363 · mode 968 · item 431
* ``mode``      rift 3582 · arena 740 · aram 589 · classic 84
* ``ability``   "" 3987 · Q 333 · W 189 · E 174 · R 174 · 被动 132 · QE 3 · WQ 2 · RW 1
* ``field_key`` damage 726 · cooldown 219 · unknown 198 · attack_damage 149 ·
  health 101 · price 82 · ability_power 81 · armor 73 · attack_speed 70 ·
  movement_speed 63 · heal 62 · ability_haste 56 · crit 56 · cost 54 · duration 42 ·
  shield 41 · slow 18 · resistances 15 · stacks 14 · tenacity 14 · exp 14 ·
  magic_resist 13 · lifesteal 12 · health_regen 11 · max_rank 10 · speed 5 ·
  magic_pen 4 · cast_time 3 · range 2 · armor_pen 2 · stolen_stat 2

Two consequences shaped the design:

1. ``damage`` alone is 33% of structured rows, so a flat one-level field list would
   be dominated by one category and "is this damage?" would be a near-useless
   question. The field level is therefore **two-tier**: family → specific key.
2. Half the structured rows carry no ability (3987/4995 chunks are rows where the
   announcement's change line sits outside a ``Q - 技能名`` heading), so ability is
   frequently *genuinely* unknown — it is the slot where semantic fallback earns its
   keep, and where an absent answer ("no ability in this text") is common and correct.
"""

from __future__ import annotations

from patch_schema import FIELD_LABELS, FIELDS

# --------------------------------------------------------------------------- intents
# Mirrors the branches that already exist in patch_engine.chat, in the order they are
# tested there. The router only asks about intent when the deterministic rules left it
# open (no overview/aggregate/claim/why marker), which is the "what does this person
# actually want" case: one number, or the whole story.
INTENTS = (
    "single_fact",       # one value for one subject, optionally one ability
    "subject_overview",  # everything that changed about one subject in one patch
    "patch_overview",    # what changed in a patch (listing)
    "cross_patch_history",  # one subject across many patches (timeline/aggregate)
    "direction_claim",   # "was X nerfed?" — a claim to check
    "explanation",       # "why was X changed" — wants the design note
    "unsupported",       # out of scope for this corpus
)
INTENT_LABELS = {
    "single_fact": "单点数值",
    "subject_overview": "对象总览",
    "patch_overview": "版本清单",
    "cross_patch_history": "跨版本历史",
    "direction_claim": "方向断言",
    "explanation": "设计说明",
    "unsupported": "超出语料范围",
}

# ------------------------------------------------------------------------ field tree
# family -> specific keys. Every key in patch_schema.FIELDS appears in exactly one
# family, except the ones deliberately left in ``unknown`` (they exist in the schema
# but are rare enough that a semantic choice among them is not worth a model call).
FIELD_FAMILIES: dict[str, tuple[str, ...]] = {
    "damage": ("damage", "attack_damage", "ability_power", "crit", "attack_speed"),
    "durability": (
        "health", "health_regen", "armor", "magic_resist", "resistances",
        "shield", "heal", "lifesteal", "tenacity",
    ),
    "tempo": ("cooldown", "ability_haste", "cast_time", "duration"),
    "resource": ("cost", "mana"),
    "mobility": ("movement_speed", "slow", "range", "speed"),
    "progression": ("price", "exp", "stacks", "max_rank"),
    "penetration": ("magic_pen", "armor_pen", "stolen_stat"),
}
FIELD_FAMILY_LABELS = {
    "damage": "伤害类",
    "durability": "生存类",
    "tempo": "节奏类",
    "resource": "资源类",
    "mobility": "机动类",
    "progression": "成长与经济",
    "penetration": "穿透与偷取",
    "unknown": "未归类",
}
# Reverse index, built from the table above so it cannot drift.
FIELD_TO_FAMILY: dict[str, str] = {
    key: family for family, keys in FIELD_FAMILIES.items() for key in keys
}
# Keys the schema knows but no family claims; kept explicit instead of silently absent.
UNCLASSIFIED_FIELDS = tuple(key for key, _ in FIELDS if key not in FIELD_TO_FAMILY)


def family_of(field_key: str) -> str:
    """Family for a canonical field key; ``unknown`` when the schema has no home for it."""
    return FIELD_TO_FAMILY.get(field_key or "", "unknown")


def families_of(field_keys: list[str]) -> list[str]:
    """Distinct families, preserving the caller's order (schema specificity order)."""
    seen: list[str] = []
    for key in field_keys or []:
        family = family_of(key)
        if family not in seen:
            seen.append(family)
    return seen


def field_label(field_key: str) -> str:
    return FIELD_LABELS.get(field_key, field_key)


def family_label(family: str) -> str:
    return FIELD_FAMILY_LABELS.get(family, family)


def family_choices(field_keys: list[str] | None = None) -> dict[str, str]:
    """Choice payload for the field-family node: ``{family: description}``.

    When the question already produced field keys, their families are named first and
    the rest stay as alternatives — the router may still need them if the retrieval
    for the hinted family comes back empty, and hiding them would make the beam blind.
    """
    hinted = families_of(list(field_keys or []))
    ordered = hinted + [name for name in FIELD_FAMILY_LABELS if name not in hinted]
    choices = {}
    for family in ordered:
        if family == "unknown":
            continue
        keys = FIELD_FAMILIES.get(family, ())
        labels = "、".join(field_label(key) for key in keys)
        choices[family] = f"{FIELD_FAMILY_LABELS[family]}（{labels}）"
    choices["unknown"] = "无法归入以上任何一类，或问题没有指明数值类型"
    return choices


def field_choices(family: str) -> dict[str, str]:
    """Choice payload for the second (specific-field) tier inside one family."""
    if family == "unknown":
        return {}
    return {key: f"{field_label(key)}（公告里的字段名示例：{_example_label(key)}）" for key in FIELD_FAMILIES.get(family, ())}


def _example_label(key: str) -> str:
    """A concrete announcement label for a key, so the choice is anchored in real wording."""
    return FIELD_LABELS.get(key, key)


# ----------------------------------------------------------------------- other nodes
# Entity types as they actually appear in the corpus.
ENTITY_TYPES = ("champion", "item", "system")
ENTITY_TYPE_LABELS = {"champion": "英雄", "item": "装备", "system": "系统/机制"}
# "mode" exists in the corpus as a ``type`` for section-level rows, but a *question*
# about a mode is a scope question, not an entity question, so it is not offered here.

# Ability node. "base" is the corpus's empty ability (base stats); "被动" is its own
# column because announcements label it 被动 rather than P.
ABILITIES = ("base", "被动", "Q", "W", "E", "R", "other")
ABILITY_LABELS = {
    "base": "基础属性/无技能归属",
    "被动": "被动",
    "Q": "Q",
    "W": "W",
    "E": "E",
    "R": "R",
    "other": "其它（多技能组合，如 QE / RW）",
}

# Scope node. ``unspecified`` is a real branch, not a missing value: rift is the
# default scope, and a question that never mentions a mode should search rift first
# while keeping the other modes reachable.
MODES = ("rift", "aram", "arena", "classic", "unspecified")
MODE_LABELS = {
    "rift": "召唤师峡谷（默认）",
    "aram": "极地大乱斗",
    "arena": "斗魂竞技场",
    "classic": "经典/怀旧模式",
    "unspecified": "问题没有指定模式",
}

# Node order for a route path. The router walks these levels in this order and stops
# as soon as a level is deterministically known — that is what makes this hierarchical
# rather than a single flat classification.
ROUTE_NODES = ("intent", "mode", "type", "ability", "field")

# Which node each fallback-eligible plan slot maps onto. ``field_keys`` in the plan is
# the resolved value of the two-tier ``field`` node, so the node name and the slot name
# differ on purpose.
SLOT_TO_NODE = {
    "intent": "intent",
    "mode": "mode",
    "type": "type",
    "ability": "ability",
    "field": "field",
}
