"""Query plan: the parse result plus *how each slot was decided*.

The old pipeline returned a plain dict of slot values, so a reader could see
``ability = "W"`` but not whether that came from an explicit letter in the text, an
ability-name lookup, a Jev semantic fallback, or a default. That distinction is the
whole point of the semantic-routing version: a deterministically resolved slot must
never be re-guessed by a model, and an unresolved slot is exactly where a model is
allowed to help.

So every slot carries a :class:`Slot` (value + provenance + confidence) instead of a
bare value. ``QueryPlan`` is the single object the engine, the router, the API
payload and the frontend all read; it serialises to JSON with
:meth:`QueryPlan.to_json` and is *not* a framework — just dataclasses with a fixed
vocabulary of slot names, sources and statuses.

Two invariants this module exists to protect:

1. ``source`` is never invented. Only ``deterministic``, ``alias``, ``semantic``,
   ``default`` and ``none`` exist, and each one maps to a specific code path.
2. A slot with ``source == "deterministic"`` or ``"alias"`` is *closed*: the engine
   reads it directly and no semantic fallback may overwrite it (see
   :meth:`QueryPlan.resolved_deterministically`).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any

# Provenance vocabulary. Keep this small and exhaustive: the frontend legend, the
# routing evaluation and the tests all switch on these exact strings.
SOURCE_DETERMINISTIC = "deterministic"
SOURCE_ALIAS = "alias"
SOURCE_SEMANTIC = "semantic"
SOURCE_DEFAULT = "default"
SOURCE_NONE = "none"
SOURCES = (SOURCE_DETERMINISTIC, SOURCE_ALIAS, SOURCE_SEMANTIC, SOURCE_DEFAULT, SOURCE_NONE)

# Per-slot status, so the UI can render a node without inferring anything.
STATUS_RESOLVED = "resolved"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNRESOLVED = "unresolved"
STATUS_UNSUPPORTED = "unsupported"

# Where a patch value came from. ``ui_default`` is the version selector in the UI and
# is deliberately distinct from a value the question itself contained.
PATCH_EXPLICIT = "explicit"
PATCH_UI_DEFAULT = "ui_default"
PATCH_SYSTEM_DEFAULT = "system_default"
PATCH_PREVIOUS = "previous"
PATCH_OUTSIDE = "outside"

SLOT_PATCHES = "patches"
SLOT_INTENT = "intent"
SLOT_MODE = "mode"
SLOT_TYPE = "type"
SLOT_SUBJECT = "subject"
SLOT_ABILITY = "ability"
SLOT_FIELD = "field"
SLOT_DIRECTION = "direction"

# Slots a semantic fallback is allowed to decide. ``subject`` is deliberately absent:
# an entity must first be narrowed by alias/lexical matching (see
# ``routing.subject_shortlist``) because choosing one name out of ~170 champions and
# ~85 items is not a classification problem, it is a search problem.
FALLBACK_SLOTS = (SLOT_INTENT, SLOT_ABILITY, SLOT_FIELD, SLOT_MODE, SLOT_TYPE)


@dataclass
class Slot:
    """One decided (or undecided) field of the plan, with its provenance."""

    name: str
    value: Any = None
    source: str = SOURCE_NONE
    status: str = STATUS_UNRESOLVED
    confidence: float | None = None
    # Free-text justification shown in the UI and the trace ("显式 W", "「真实伤害」规则").
    why: str = ""
    # Ranked alternatives considered for this slot: [{"value":…, "probability":…}, …].
    # Filled by the semantic router when it had more than one plausible reading.
    candidates: list[dict] = dataclass_field(default_factory=list)

    @property
    def closed(self) -> bool:
        """True when the value was obtained without a model and must not be re-guessed."""
        return self.source in (SOURCE_DETERMINISTIC, SOURCE_ALIAS) and self.value not in (None, "", [])

    def to_json(self) -> dict:
        payload = {
            "name": self.name,
            "value": self.value,
            "source": self.source,
            "status": self.status,
            "why": self.why,
        }
        if self.confidence is not None:
            payload["confidence"] = round(float(self.confidence), 4)
        if self.candidates:
            payload["candidates"] = self.candidates
        return payload


@dataclass
class RoutePath:
    """One candidate search path through the taxonomy, with its branch score."""

    # [{"node": "ability", "value": "W", "probability": 0.91}, …]
    nodes: list[dict] = dataclass_field(default_factory=list)
    # Geometric mean of the per-node probabilities — see routing.path_score for why
    # this is used instead of a plain product.
    score: float = 0.0
    source: str = SOURCE_NONE
    # Filled in by the retrieval stage so the UI can show what each branch produced.
    where: dict = dataclass_field(default_factory=dict)
    candidate_count: int = 0
    contributed: bool = False
    detail: str = ""

    @property
    def label(self) -> str:
        return " → ".join(f"{node.get('node')}={node.get('value')}" for node in self.nodes) or "unfiltered"

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "nodes": self.nodes,
            "score": round(float(self.score), 4),
            "source": self.source,
            "where": self.where,
            "candidate_count": self.candidate_count,
            "contributed": self.contributed,
            "detail": self.detail,
        }


@dataclass
class QueryPlan:
    """Everything the engine knows about a question before it searches."""

    question: str = ""
    slots: dict[str, Slot] = dataclass_field(default_factory=dict)

    # --- patch bookkeeping (kept alongside the slot: it is a list plus a provenance) ---
    patches: list[str] = dataclass_field(default_factory=list)
    patch_source: str = PATCH_SYSTEM_DEFAULT
    outside: list[str] = dataclass_field(default_factory=list)
    span: list[str] | None = None
    aggregate: bool = False
    overview: bool = False
    why: bool = False
    ability_from_name: str = ""
    unresolved_slots: list[str] = dataclass_field(default_factory=list)

    # --- routing (filled by routing.SemanticRouter; empty when routing is off) ---
    route_paths: list[RoutePath] = dataclass_field(default_factory=list)
    routing_confidence: float | None = None
    beam_used: bool = False
    routing_mode: str = "off"
    routing_notes: list[str] = dataclass_field(default_factory=list)

    # ------------------------------------------------------------------ helpers

    def get(self, name: str, default: Any = None) -> Slot:
        return self.slots.get(name) or Slot(name=name, value=default)

    def value(self, name: str, default: Any = None) -> Any:
        slot = self.slots.get(name)
        return default if slot is None or slot.value in (None, "", []) else slot.value

    def set_slot(
        self,
        name: str,
        value: Any,
        source: str,
        status: str | None = None,
        confidence: float | None = None,
        why: str = "",
        candidates: list[dict] | None = None,
    ) -> Slot:
        if status is None:
            status = STATUS_RESOLVED if value not in (None, "", []) else STATUS_UNRESOLVED
        slot = Slot(
            name=name,
            value=value,
            source=source,
            status=status,
            confidence=confidence,
            why=why,
            candidates=list(candidates or []),
        )
        self.slots[name] = slot
        return slot

    def resolved_deterministically(self, name: str) -> bool:
        """Whether a model is forbidden from touching this slot."""
        return self.get(name).closed

    def refresh_unresolved(self) -> list[str]:
        """Recompute which fallback-eligible slots still need a decision."""
        self.unresolved_slots = [
            name
            for name in FALLBACK_SLOTS
            if not self.get(name).closed and self.get(name).value in (None, "", [])
        ]
        return self.unresolved_slots

    # -------------------------------------------------------------- serialisation

    def to_json(self) -> dict:
        """The payload the API returns and the frontend draws. No computed prose."""
        return {
            "question": self.question,
            "patches": list(self.patches),
            "patch_source": self.patch_source,
            "outside": list(self.outside),
            "span": list(self.span) if self.span else None,
            "aggregate": self.aggregate,
            "overview": self.overview,
            "why": self.why,
            "ability_from_name": self.ability_from_name,
            "slots": {name: slot.to_json() for name, slot in self.slots.items()},
            "unresolved_slots": list(self.unresolved_slots),
            "routing": {
                "mode": self.routing_mode,
                "beam_used": self.beam_used,
                "confidence": None if self.routing_confidence is None else round(float(self.routing_confidence), 4),
                "paths": [path.to_json() for path in self.route_paths],
                "notes": list(self.routing_notes),
            },
        }
