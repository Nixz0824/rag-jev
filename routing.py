"""Semantic router: where a model is allowed to decide, and how far it may branch.

This module owns the policy that ``jev.py`` deliberately does not: the taxonomy walk,
the decision to call a model for one slot, the confidence gate that turns a close call
into two search branches instead of one, and every budget that keeps a single question
from turning into ten API calls.

**The rule that matters most.** The deterministic parser runs first and closes every
slot it can. Only a slot that came back ``unresolved``/``ambiguous`` is eligible for a
model, and a slot resolved by regex or alias is *never* re-asked — see
``QueryPlan.resolved_deterministically``. ``26.17 薇恩 W 真实伤害是多少`` therefore
produces zero model calls: patch by regex, subject by alias, ability by the explicit
letter, field by the ``伤害`` rule.

**Routing mode** (``RAGJEV_ROUTING_MODE``) keeps a measurable baseline:

``off``     never call a model for understanding; behave exactly like 0.13.0.
``shadow``  do the semantic work, record it, but do not change retrieval.
``active``  use the routed branches for retrieval.

**Path score.** A path is a chain of nodes (``ability=W`` → ``field=damage``). Naively
multiplying node probabilities punishes long paths for being long, so the score is the
**geometric mean** of the probabilities of the semantic nodes on the path:
``score = exp(mean(log(p_i)))``. Deterministically known nodes contribute no factor
(they are not uncertain, so they are not evidence either way). This keeps scores on the
same 0–1 scale as a single probability, makes them comparable between a 1-node and a
3-node path, and is trivial to assert in tests. A path with no semantic node scores 1.0.

**Beam rule.** A close second reading is not noise: if two categories are genuinely
ambiguous, killing the loser before retrieval deletes evidence that was never looked
at. So the router keeps a second path when the runner-up's probability is at least
``beam_ratio × top`` (default: half). Concretely ``0.93/0.04/0.03`` keeps one path and
``0.48/0.44/0.08`` keeps two. The thresholds are *measured*, not asserted — see
``scripts/evaluate_routing.py`` and ``docs/Jev分层路由.md``.

**The safety net.** Whenever the router branches *or* declines to decide, it appends one
unconstrained path that carries no ability/field filter. A wrong model reading can then
cost precision, but it can never make the correct row unreachable. That guarantee is
what allows the confidence bar to be low and the beam to be wide.
"""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass

import jev
import taxonomy
from patch_schema import FIELDS, MODE_HINTS

# key -> the regex the deterministic parser matches against a question. Declared here as a
# lookup so the router can ask "did the user actually name this field?" without duplicating
# any pattern: `patch_schema.FIELDS` stays the single source of truth.
FIELD_PATTERNS = {key: pattern for key, pattern in FIELDS}
from query_plan import (
    FALLBACK_SLOTS,
    SOURCE_DETERMINISTIC,
    SOURCE_NONE,
    SOURCE_SEMANTIC,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    QueryPlan,
    RoutePath,
)

# --------------------------------------------------------------------------- modes
MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ACTIVE = "active"
ROUTING_MODES = (MODE_OFF, MODE_SHADOW, MODE_ACTIVE)


def routing_mode_from_env(default: str = MODE_ACTIVE) -> str:
    """Read ``RAGJEV_ROUTING_MODE``, tolerating case and whitespace."""
    value = (os.environ.get("RAGJEV_ROUTING_MODE") or "").strip().lower()
    return value if value in ROUTING_MODES else default


def field_widening_from_env(default: bool = True) -> bool:
    """Read ``RAGJEV_FIELD_WIDENING`` (``0``/``off`` disables the sibling widening).

    Kept as a switch so the effect of admitting sibling field keys can be measured rather
    than argued about: the 40-case effect set is run both ways and the numbers decide.
    """
    value = (os.environ.get("RAGJEV_FIELD_WIDENING") or "").strip().lower()
    if not value:
        return default
    return value not in ("0", "off", "false", "no")


# ------------------------------------------------------------------------ thresholds
@dataclass
class RoutingConfig:
    """Every number the router uses, in one place, with its provenance.

    These are deliberately **separate** from the rerank gate
    (``jev.MIN_RERANK_GAP = 0.15``) because they answer a different question: rerank
    asks "are these two *answers* distinguishable", routing asks "are these two *search
    scopes* distinguishable". The confidence bar is intentionally low, because searching
    one extra branch is cheap (a BM25+vector pass over an already metadata-filtered
    slice) while silently dropping the right branch is not recoverable. Measurement and
    its limits are reported in ``docs/Jev分层路由.md``.
    """

    # Below this probability a model's answer is recorded but not acted upon: it is a
    # guess, and an unfiltered path is strictly safer because it retrieves more.
    min_fallback_confidence: float = 0.35
    # Above this probability a reading may stand *alone*. Between the two thresholds the
    # value is still used (it is the best available reading) but a rival path is kept as
    # well — this is the difference between "which value" and "may I stop looking".
    commit_confidence: float = 0.6
    # Keep a second path when runner_up >= beam_ratio * top (see the module docstring).
    beam_ratio: float = 0.5
    beam_width: int = 2
    # Hard budgets: at most this many understanding calls, and this much wall clock for
    # the whole routing stage, per question.
    max_jev_calls: int = 2
    max_depth: int = 4
    timeout_s: float = 20.0

    def as_json(self) -> dict:
        return {
            "min_fallback_confidence": self.min_fallback_confidence,
            "commit_confidence": self.commit_confidence,
            "beam_ratio": self.beam_ratio,
            "beam_width": self.beam_width,
            "max_jev_calls": self.max_jev_calls,
            "max_depth": self.max_depth,
            "timeout_s": self.timeout_s,
        }


# Default config, importable so the evaluation and the API report the same numbers.
CONFIG = RoutingConfig()

# Rank-damping constant for fusing branch candidate lists. Matches the constant the
# retrieval kernel uses for BM25 + dense fusion (``engine.Retriever.search``), so both
# fusions in this pipeline damp ranks identically.
RRF_K = 60

# ------------------------------------------------------------------ signal detection
# Words that indicate *some* stat is being asked about without resolving to a canonical
# key. When one of these appears and the field slot is still open, the field node is
# genuinely ambiguous rather than merely absent — there is a real question to ask.
FIELD_SIGNALS = re.compile(
    r"伤害|输出|数值|数据|属性|面板|加成|收益|效果|条|多少|几|成长|"
    r"上面|那个|这类|一类|相关|之类的|百分比|真实|法术|物理|基础|额外|最大|最小|总|每秒"
)


def has_field_signal(text: str) -> bool:
    return bool(FIELD_SIGNALS.search(text))


# ------------------------------------------------------------------------- the router
class SemanticRouter:
    """Resolve open plan slots through the taxonomy, under a hard call budget.

    ``api_key`` is threaded through so tests can pass an explicit key and stub
    ``jev.classify_many``; without a key every method degrades to the deterministic plan
    and records why in ``plan.routing_notes`` and the phase ledger — never silently.
    """

    def __init__(self, retriever, config: RoutingConfig | None = None, api_key: str | None = None,
                 mode: str | None = None):
        self.r = retriever
        self.config = config or CONFIG
        self.api_key = api_key
        self.mode = mode or routing_mode_from_env()
        self.calls = 0
        self.cost_usd = 0.0
        self.latency_ms = 0
        self.started = 0.0
        self.question = ""
        # Notes raised by the fallback stage that only the routing stage can act on
        # (a close call accepted as a value still has to become two search paths).
        self.beam_notes: list[str] = []
        # Phase ledger handed back to the engine so the session can show a breakdown.
        self.phases = jev.empty_phases()

    # ------------------------------------------------------------------- plumbing

    def _budget_left(self) -> bool:
        if self.calls >= self.config.max_jev_calls:
            return False
        if self.started and (time.perf_counter() - self.started) > self.config.timeout_s:
            return False
        return bool(self.api_key or jev.available())

    def _account(self, phase: str, result: dict | None, detail: str, status: str) -> None:
        """Book one call (or its absence) against a phase. The only place ``calls`` grows."""
        if result:
            self.calls += 1
            self.cost_usd = round(self.cost_usd + float(result.get("cost_usd", 0.0)), 6)
            self.latency_ms += int(result.get("latency_ms", 0))
            jev.book(self.phases, phase, status, result.get("cost_usd", 0.0),
                     result.get("latency_ms", 0), detail, calls=1)
        else:
            jev.book(self.phases, phase, status, detail=detail)

    def _classify(self, phase: str, questions: dict[str, dict[str, str]], detail: str) -> dict | None:
        """One budgeted ``classify_many`` call, booked against ``phase``.

        Every semantic call in this module goes through here, which is what makes the
        call budget and the phase ledger impossible to bypass by accident.
        """
        if not questions or not self._budget_left():
            return None
        result = jev.classify_many(
            self.question, questions, api_key=self.api_key, timeout=int(self.config.timeout_s)
        )
        self._account(phase, result, detail, jev.PHASE_CALLED)
        return result

    @staticmethod
    def _rank(parsed: dict, node: str) -> list[dict]:
        """Normalise a choice answer into ranked ``{node, value, probability}`` nodes."""
        return [
            {"node": node, "value": value, "probability": round(float(probability), 4)}
            for value, probability in sorted(parsed["probabilities"].items(), key=lambda item: -item[1])
        ]

    # --------------------------------------------------------------- entry point

    def plan(self, plan: QueryPlan, subject_shortlist: list[tuple[str, str]] | None = None) -> QueryPlan:
        """Fill open slots and produce route paths. Never raises, never silent."""
        plan.routing_mode = self.mode
        self.question = plan.question
        self.started = time.perf_counter()

        if self.mode == MODE_OFF:
            plan.routing_notes.append("routing=off：完全使用确定性解析，不调用语义决策")
            jev.book(self.phases, "semantic_fallback", jev.PHASE_BYPASSED, detail="routing=off")
            jev.book(self.phases, "hierarchical_routing", jev.PHASE_BYPASSED, detail="routing=off")
            self._deterministic_route(plan)
            return plan

        if not self._budget_left():
            # Distinguish "the model is missing" from "the model was needed". A question whose
            # slots are all closed needs nothing, so reporting a degradation for it would be
            # noise; only a question with real open slots is actually degraded.
            reason = "没有 TYPESAFE_API_KEY" if not (self.api_key or jev.available()) else "初始预算不足"
            plan.refresh_unresolved()
            needed = bool(self._open_questions(plan)) or self._find_contender(
                plan, self.config.commit_confidence) is not None
            status = jev.PHASE_FALLBACK if needed else jev.PHASE_BYPASSED
            detail = f"{reason}，{len(plan.unresolved_slots)} 个槽位保持未决" if needed else f"{reason}，本次无需判断"
            plan.routing_notes.append(
                f"routing={self.mode}：{reason}，退回确定性流程" if needed
                else f"routing={self.mode}：{reason}，但本次没有需要模型判断的槽位"
            )
            jev.book(self.phases, "semantic_fallback", status, detail=detail)
            jev.book(self.phases, "hierarchical_routing", status, detail=detail)
            self._deterministic_route(plan)
            return plan

        plan.refresh_unresolved()
        self._resolve_subject(plan, subject_shortlist or [])
        self._semantic_fallback(plan)
        self._hierarchical_route(plan)
        return plan

    def _open_questions(self, plan: QueryPlan) -> dict[str, dict[str, str]]:
        """Slots that are open *and* have something real to ask about.

        A slot can be open without being askable — an ability question with no resolved
        subject has no meaningful Q/W/E/R descriptions — and treating those as work would
        book a model call that never happens.
        """
        questions: dict[str, dict[str, str]] = {}
        for name in FALLBACK_SLOTS:
            slot = plan.get(name)
            if slot.closed or slot.status == STATUS_RESOLVED:
                continue
            choices = self._choices_for(plan, name)
            if choices:
                questions[name] = choices
        return questions

    # ------------------------------------------------------------- subject shortlist

    def _resolve_subject(self, plan: QueryPlan, shortlist: list[tuple[str, str]]) -> None:
        """Decide the entity from an already-narrowed shortlist, never from the full table.

        ``shortlist`` is ``[(display_subject, reason), …]`` from the engine's lexical/fuzzy
        candidate generation. Choosing one name among ~170 champions and ~85 items is a
        search problem, not a classification problem, so the model only ever sees
        candidates the question text is already close to.
        """
        slot = plan.get("subject")
        if slot.closed or slot.status == STATUS_RESOLVED:
            return
        if not shortlist:
            return
        if len(shortlist) == 1:
            subject, reason = shortlist[0]
            plan.set_slot("subject", subject, SOURCE_DETERMINISTIC, STATUS_RESOLVED,
                          why=f"词形候选唯一：{reason}")
            return
        result = jev.shortlist(
            plan.question,
            {f"c{index}": f"{name}（{reason}）" for index, (name, reason) in enumerate(shortlist)},
            api_key=self.api_key,
        ) if self._budget_left() else None
        self._account("semantic_fallback", result, f"对象候选 {len(shortlist)} 个",
                      jev.PHASE_CALLED if result else jev.PHASE_FALLBACK)
        if not result or result["choice"] == "none":
            plan.routing_notes.append(
                "对象候选存在但语义选择未采纳，保持未决（问答会转为追问）"
                if result else "对象候选存在但语义调用失败，保持未决"
            )
            return
        index = int(str(result["choice"]).removeprefix("c"))
        if not 0 <= index < len(shortlist):
            plan.routing_notes.append("语义返回了候选之外的对象，未采纳")
            return
        subject, reason = shortlist[index]
        plan.set_slot(
            "subject", subject, SOURCE_SEMANTIC, STATUS_RESOLVED,
            confidence=result["confidence"],
            why=f"语义在 {len(shortlist)} 个词形候选里选择（{reason}）",
            candidates=[
                {"value": name, "probability": round(float(result["probabilities"].get(f"c{pos}", 0.0)), 4)}
                for pos, (name, _) in enumerate(shortlist)
            ],
        )
        plan.routing_notes.append(f"对象由语义选择：{subject}（{result['confidence']:.2f}）")

    # ------------------------------------------------------------ semantic fallback

    def _semantic_fallback(self, plan: QueryPlan) -> None:
        """Ask about the slots the deterministic parser left open, in a single call.

        Slots that are already closed are not put into the questions at all, which makes
        "ability is explicit → no model call for ability" structural rather than a comment.
        """
        open_slots = self._open_questions(plan)
        if not open_slots:
            resolved = "、".join(
                f"{name}={plan.value(name)}" for name in FALLBACK_SLOTS if plan.get(name).closed
            )
            jev.book(self.phases, "semantic_fallback", jev.PHASE_BYPASSED,
                     detail=f"需要判断的槽位已由规则确定（{resolved or '无待定槽位'}）")
            plan.routing_notes.append("语义补全未触发：待判槽位已由确定性规则确定")
            return
        detail = "待定槽位：" + "、".join(open_slots)
        result = self._classify("semantic_fallback", open_slots, detail)
        if not result:
            jev.book(self.phases, "semantic_fallback", jev.PHASE_FALLBACK,
                     detail=f"调用失败，保持未决（{detail}）")
            plan.routing_notes.append("语义补全调用失败，退回确定性流程，未决槽位保持未决")
            return

        for name, parsed in (result.get("slots") or {}).items():
            slot = plan.get(name)
            confidence = float(parsed["confidence"])
            # "unspecified" (and "other") are real answers that mean "no decision here";
            # treating them as a value would invent a filter the question never implied.
            declined = parsed["choice"] in ("unspecified", "other", "none")
            accepted = confidence >= self.config.min_fallback_confidence and not declined
            slot.source = SOURCE_SEMANTIC
            slot.confidence = confidence
            slot.candidates = self._rank(parsed, name)
            slot.why = (f"语义分类 {parsed['choice']}（{confidence:.2f}）"
                        + ("，模型表示未指明，不采纳" if declined
                           else "" if accepted
                           else "，低于补全门槛，不采纳"))
            # Rejected: keep the slot open for the router to branch on rather than
            # silently committing to a low-probability reading.
            slot.value = parsed["choice"] if accepted else None
            slot.status = STATUS_RESOLVED if accepted else STATUS_UNRESOLVED
            if accepted and confidence < self.config.commit_confidence:
                # Accepted as the best available value, but not confident enough to be the
                # only reading: the routing stage will keep a rival path alongside it.
                self.beam_notes.append(
                    f"{name} 命中 {parsed['choice']} 但置信度 {confidence:.2f} 低于单独采信门槛 "
                    f"{self.config.commit_confidence:.2f}，保留竞争读法"
                )
            if name == "field":
                # The field node is two-tier; ``_apply_field_choice`` records which keys
                # the accepted family implies without inventing a plan slot for them.
                self._apply_field_choice(plan, parsed["choice"], confidence, slot.candidates)

        plan.refresh_unresolved()
        accepted = [name for name in open_slots if plan.get(name).status == STATUS_RESOLVED]
        plan.routing_notes.append(
            f"语义补全：{'、'.join(accepted) if accepted else '无采纳'}（{len(open_slots)} 个待定槽位，1 次调用）"
        )

    def _choices_for(self, plan: QueryPlan, name: str) -> dict[str, str]:
        """Criteria for one slot, or ``{}`` when the router has nothing real to ask."""
        if name == "ability":
            subject = plan.value("subject")
            names = (self.r.abilities.get(subject) or {}) if subject else {}
            if not names:
                # Without a known subject the options cannot be described by skill names,
                # and "base vs passive vs Q" alone is not a question a model can answer.
                return {}
            choices: dict[str, str] = {}
            for key in ("被动", "Q", "W", "E", "R"):
                skill = names.get(key)
                choices[key] = f"{key}（{skill}）" if skill else key
            choices["base"] = "基础属性（生命/护甲/攻击力等，不属于任何技能）"
            return choices
        if name == "field":
            hinted = plan.get("field_keys").value or []
            if not hinted and not has_field_signal(plan.question):
                return {}
            return taxonomy.family_choices(hinted)
        if name == "intent":
            return dict(taxonomy.INTENT_LABELS)
        if name == "mode":
            # Only ask when the question actually mentions a scope. Without a mode hint
            # there is nothing to classify — the answer would be "unspecified" every time,
            # so paying a model call for it would be spending money to learn nothing.
            if not any(hint in plan.question for _, hints in MODE_HINTS for hint in hints):
                return {}
            # ``unspecified`` is offered explicitly so the model can decline instead of being
            # forced to name a mode the user never mentioned.
            return {"unspecified": taxonomy.MODE_LABELS["unspecified"],
                    **{mode: label for mode, label in taxonomy.MODE_LABELS.items()
                       if mode not in ("rift", "unspecified")}}
        if name == "type":
            if plan.value("subject") or not re.search(r"英雄|装备|物品|系统|机制|符文", plan.question):
                return {}
            return dict(taxonomy.ENTITY_TYPE_LABELS)
        return {}

    def _apply_field_choice(self, plan: QueryPlan, family: str, confidence: float,
                            candidates: list[dict]) -> None:
        """Record which concrete field keys an accepted field-family choice implies.

        The keys live on the plan's ``field_keys`` slot (a real slot the engine already
        filters on), so no new vocabulary is introduced for the routing case.
        """
        keys = list(taxonomy.FIELD_FAMILIES.get(family, ()))
        if not keys:
            return
        # The candidates list is the *family-level* distribution, so a reader can see which
        # other family was in play even though the committed value is a list of keys.
        plan.set_slot(
            "field_keys", keys, SOURCE_SEMANTIC, STATUS_RESOLVED, confidence=confidence,
            why=f"语义判定字段族 {taxonomy.family_label(family)}，展开为 {len(keys)} 个候选键",
            candidates=[{"value": family, "probability": confidence},
                        *[item for item in candidates if item.get("value") != family]],
        )

    # --------------------------------------------------------- hierarchical routing

    def widened_field_keys(self, keys: list[str], question: str = "") -> list[str]:
        """Add sibling field keys that the question's own wording justifies.

        The corpus schema files a row under one key while the announcement's label can say
        something else: Jax's "额外护甲和魔法抗性" lands under ``armor``, not ``resistances``.
        Filtering on the exact key therefore *drops* the answer — measured on the 40-case
        effect set, exact filtering removed the correct row in 5 cases while improving the rank
        in 1.

        Widening to the whole family is too blunt: the durability family has nine keys, so
        adding all of them pulls in unrelated rows (health, shield, heal) that crowd out the
        right one — measured at active 30/40 → 29.3/40 with two newly harmed cases. Instead a
        sibling is admitted only when the question itself contains that field's label text,
        which keeps the widening evidence-based: the user said "护甲", so the ``armor`` row is
        in scope; they never said "生命值", so the health rows are not.

        Exact keys are always kept, so this remains a superset of the old behaviour and cannot
        lose a row that used to be retrievable.
        """
        widened = list(dict.fromkeys(keys))
        if not field_widening_from_env():
            return widened
        text = question or ""
        for key in keys:
            family = taxonomy.FIELD_TO_FAMILY.get(key, "")
            for sibling in taxonomy.FIELD_FAMILIES.get(family, ()):
                if sibling in widened or sibling not in FIELD_PATTERNS:
                    continue
                # The sibling is admitted on the user's own words: `FIELD_PATTERNS` is the same
                # key -> pattern table the deterministic parser reads, so "护甲" in the question
                # is what puts the `armor` row back in scope — not schema adjacency.
                if re.search(FIELD_PATTERNS[sibling], text, re.I):
                    widened.append(sibling)
        return widened

    def base_where(self, plan: QueryPlan) -> dict:
        """Metadata filter shared by every branch, built once from resolved slots."""
        where: dict = {}
        if plan.value("subject"):
            where["subject"] = plan.value("subject")
        if plan.value("ability"):
            where["ability"] = plan.value("ability")
        if not plan.value("subject") and plan.value("type"):
            where["type"] = plan.value("type")
        where["patches"] = list(plan.patches)
        if plan.value("mode"):
            where["mode"] = plan.value("mode")
        if not plan.value("subject") and plan.value("direction"):
            where["direction"] = plan.value("direction")
        return where

    def _deterministic_route(self, plan: QueryPlan) -> None:
        """The routing-off path: exactly one path, built only from resolved slots."""
        path = RoutePath(nodes=[], source=SOURCE_DETERMINISTIC, where=self.base_where(plan),
                         detail="确定性解析结果，未做语义分叉", score=1.0)
        field_keys = plan.get("field_keys").value
        if field_keys:
            path.where["field_keys"] = self.widened_field_keys(field_keys, plan.question)
        plan.route_paths = [path]
        plan.routing_confidence = 1.0
        plan.beam_used = False

    def _hierarchical_route(self, plan: QueryPlan) -> None:
        """Walk the taxonomy for still-open nodes and emit one or more search paths.

        The whole walk is **one batched call** where possible. The questions are
        independent, so asking them separately would multiply cost and latency for no
        accuracy gain; a second call is only spent when the first answered and budget
        remains.
        """
        plan.refresh_unresolved()
        # ``field_keys`` is left to the semantic fallback stage (it decides the *family*),
        # so the field node is not re-asked here. Its second tier — which specific key
        # inside an already-decided family — is what this stage may ask.
        nodes = [node for node in taxonomy.ROUTE_NODES
                 if node != "field" and node in plan.unresolved_slots]
        second_tier_family = (
            plan.get("field").value
            if plan.get("field").source == SOURCE_SEMANTIC
            and plan.get("field").value in taxonomy.FIELD_FAMILIES
            and not plan.get("field_keys").value
            else None
        )
        # Nothing left to *ask* is not the same as nothing left to *do*: a slot the fallback
        # accepted below ``commit_confidence`` still has to become a second search path, and
        # that decision is made here rather than in the fallback stage.
        has_contender = self._find_contender(plan, self.config.commit_confidence) is not None

        started = time.perf_counter()
        attempts = 0
        branches: dict[str, list[dict]] = {}
        field_second_tier: list[dict] = []
        confidences: list[float] = []

        questions: dict[str, dict[str, str]] = {}
        for node in nodes[: self.config.max_depth]:
            choices = self._route_choices(plan, node)
            if choices:
                questions[node] = choices
        if second_tier_family:
            tier_choices = taxonomy.field_choices(second_tier_family)
            if tier_choices:
                questions["field_key"] = tier_choices

        if questions:
            result = self._classify("hierarchical_routing", questions,
                                    "层级：" + "、".join(questions))
            attempts += 1
            for name in questions:
                parsed = (result or {}).get("slots", {}).get(name)
                if not parsed:
                    plan.routing_notes.append(f"层级 {name} 未得到可用答复，保持未决")
                    continue
                confidence = float(parsed["confidence"])
                if name == "field_key":
                    # The second tier refines a family that is already decided, so it is
                    # recorded even at low confidence: it only narrows inside that family.
                    field_second_tier = self._rank(parsed, "field_key")
                    confidences.append(confidence)
                    plan.routing_notes.append(f"字段二级：{parsed['choice']}（{confidence:.2f}）")
                    continue
                branches[name] = self._rank(parsed, name)
                confidences.append(confidence)
                if confidence < self.config.min_fallback_confidence:
                    plan.routing_notes.append(
                        f"层级 {name} 首选 {parsed['choice']} 置信度 {confidence:.2f} 偏低，"
                        "保留为分叉而不是唯一路径"
                    )
                else:
                    plan.routing_notes.append(f"层级 {name}：{parsed['choice']}（{confidence:.2f}）")

        # Build the paths from whatever is now known. A node that was open but had nothing
        # worth asking about (a scope node with no scope in the text) yields no branch and
        # is not an error; the plan's accepted semantic values still become the path.
        paths = self._build_paths(plan, branches, field_second_tier)
        readings = [path for path in paths if path.nodes]
        if not readings:
            # No model decided anything about this question, so retrieve exactly as the
            # deterministic pipeline would. Recorded as bypassed rather than as routing work.
            jev.book(self.phases, "hierarchical_routing", jev.PHASE_BYPASSED,
                     detail="没有需要判断的层级，且没有语义决策，按单一路径检索")
            plan.routing_notes.append("分层路由未触发：所有层级都由确定性规则确定，无需分叉")
            self._deterministic_route(plan)
            return

        plan.route_paths = paths
        # ``beam_used`` counts distinct *readings*, not paths: the conservative safety net is
        # not an alternative interpretation of the question, it is a recall guarantee. Calling
        # it a beam would overstate what the router did.
        plan.beam_used = len(readings) > 1
        plan.routing_confidence = (round(min(confidences), 4) if confidences
                                   else self._lowest_semantic_confidence(plan))
        if not questions:
            # No new call was needed, but a fork decision still happened here: a slot the
            # fallback accepted below the commit bar became a second search path. Booking it
            # against this phase (rather than bypassed) keeps the ledger honest.
            jev.book(self.phases, "hierarchical_routing", jev.PHASE_CALLED,
                     detail=f"未新增调用；把已确定的语义槽位整理为 {len(readings)} 条读法")
            plan.routing_notes.append(
                f"分层路由未新增调用：语义补全已确定全部层级，整理为 {len(readings)} 条检索读法"
            )
        else:
            plan.routing_notes.append(
                f"分层路由：判断 {len(branches) + (1 if field_second_tier else 0)} 个层级、"
                f"{attempts} 次调用、{int((time.perf_counter() - started) * 1000)}ms"
            )
        plan.routing_notes.append(
            f"分叉触发（beam={len(readings)}）：存在与首选接近的读法，多条路径都检索"
            if plan.beam_used else "分叉未触发：首选明显领先，只走单一路径"
        )

    def _route_choices(self, plan: QueryPlan, node: str) -> dict[str, str]:
        """Criteria for one routing node."""
        return self._choices_for(plan, node)

    def _build_paths(self, plan: QueryPlan, branches: dict[str, list[dict]],
                     field_second_tier: list[dict]) -> list[RoutePath]:
        """Turn per-node distributions into concrete retrieval paths.

        Always ends with one unconstrained path (see the module docstring): the promise
        that a misread can cost precision but never hide the right row.
        """
        # Path nodes describe every *semantic* decision on this route, including ones the
        # fallback stage accepted without a second call. Without this, a path built only from
        # freshly-asked branch nodes would mislabel itself as unfiltered, and a contender
        # would have no node to swap.
        primary: dict[str, dict] = {}
        for name in ("intent", "mode", "type", "ability", "field"):
            slot = plan.get(name)
            if slot.source != SOURCE_SEMANTIC or slot.value in (None, "", []):
                continue
            primary[name] = {"node": name, "value": slot.value,
                             "probability": round(float(slot.confidence or 0.0), 4)}
        for node, ranked in branches.items():
            if ranked:
                primary[node] = ranked[0]
        if field_second_tier:
            # A specific key *refines* the family node rather than sitting beside it.
            primary.pop("field", None)
            primary["field_key"] = field_second_tier[0]
        primary_nodes = list(primary.values())
        paths: list[RoutePath] = []

        def make(nodes: list[dict], detail: str) -> RoutePath:
            scoped = self.base_where(plan)
            keys = self.field_keys_for(nodes)
            if keys:
                scoped["field_keys"] = keys
            ability = next((node["value"] for node in nodes if node["node"] == "ability"), None)
            if ability and ability not in ("base", "other"):
                # "base" means "no ability filter"; filtering on it would match nothing.
                scoped["ability"] = ability
            path = RoutePath(nodes=list(nodes), source=SOURCE_SEMANTIC, where=scoped, detail=detail)
            path.score = path_score(nodes)
            return path

        def describe(nodes: list[dict]) -> str:
            return "、".join(f"{node['node']}={node['value']}" for node in nodes) or "无过滤"

        paths.append(make(primary_nodes, f"首选读法：{describe(primary_nodes)}"))

        # A slot accepted by the fallback below ``commit_confidence`` is *usable* but not
        # settled, so its rival reading gets a path of its own. This is the case the
        # fallback stage could not handle by itself: it carries one value plus the
        # distribution, not two decisions.
        contender = self._contender_path(plan, primary_nodes, make, describe)
        if contender and self.config.beam_width > 1:
            paths.append(contender)

        # A *branch* decision (a node the router asked about) whose runner-up is statistically
        # close also earns its own path.
        for node, ranked in branches.items():
            if len(ranked) < 2 or len(paths) >= self.config.beam_width:
                continue
            top, second = ranked[0], ranked[1]
            if second["probability"] < self.config.beam_ratio * max(top["probability"], 1e-6):
                continue
            alt = [{**item, "value": second["value"], "probability": second["probability"]}
                   if item["node"] == node else item for item in primary_nodes]
            if alt != primary_nodes:
                paths.append(make(alt, f"次选读法：{describe(alt)}"))

        # The safety net drops *every* semantic narrowing, not just the field one. A path
        # labelled "no ability/field filter" that still filtered by ability would silently
        # reintroduce exactly the risk it exists to remove. It is added only when a semantic
        # decision actually narrowed the search; otherwise the primary *is* the full slice.
        if any(path.nodes for path in paths) and (paths[0].where.get("ability")
                                                  or paths[0].where.get("field_keys")):
            unconstrained = self.base_where(plan)
            unconstrained.pop("ability", None)
            unconstrained.pop("field_keys", None)
            paths.append(RoutePath(
                nodes=[], source=SOURCE_DETERMINISTIC, where=unconstrained,
                detail="保守路径：不加技能/字段过滤，避免误判导致漏检", score=1.0,
            ))
        return paths[: 1 + self.config.beam_width]

    @staticmethod
    def _lowest_semantic_confidence(plan: QueryPlan) -> float | None:
        """Weakest confidence among the plan's semantic decisions, or ``None`` if none."""
        values = [
            float(slot.confidence)
            for slot in plan.slots.values()
            if slot.source == SOURCE_SEMANTIC and slot.confidence is not None
        ]
        return round(min(values), 4) if values else None

    @staticmethod
    def _find_contender(plan: QueryPlan, commit_confidence: float) -> tuple[float, str, dict] | None:
        """The weakest accepted semantic slot that is not settled enough to stand alone.

        Returns ``(confidence, slot_name, runner_up_candidate)`` or ``None``. A slot whose
        confidence sits between ``min_fallback_confidence`` and ``commit_confidence`` is the
        case the fallback stage cannot resolve by itself: it has one value (so there is
        nothing left to ask) but the question is not settled (so committing to a single
        search scope would be premature).
        """
        weakest: tuple[float, str, dict] | None = None
        for name, slot in plan.slots.items():
            if slot.source != SOURCE_SEMANTIC or slot.value is None or not slot.candidates:
                continue
            if slot.confidence is None or slot.confidence >= commit_confidence:
                continue
            runner = next((item for item in slot.candidates if item.get("value") != slot.value), None)
            if not runner:
                continue
            if weakest is None or slot.confidence < weakest[0]:
                weakest = (slot.confidence, name, runner)
        return weakest

    def _contender_path(self, plan: QueryPlan, primary: list[dict], make, describe) -> RoutePath | None:
        """Build the rival search path for the least settled accepted slot, if any."""
        weakest = self._find_contender(plan, self.config.commit_confidence)
        if weakest is None:
            return None
        confidence, name, runner = weakest
        rival = {"node": name, "value": runner["value"], "probability": runner.get("probability", 0.0)}
        alt = [{**node, "value": rival["value"], "probability": rival["probability"]}
               if node["node"] == name else node for node in primary]
        if not any(node["node"] == name for node in primary):
            alt = primary + [rival]
        if alt == primary:
            return None
        plan.routing_notes.append(
            f"{name} 置信度 {confidence:.2f} 未达单独采信门槛 {self.config.commit_confidence:.2f}，"
            f"另开一条 {runner['value']} 分支"
        )
        return make(alt, f"竞争读法：{describe(alt)}")

    @staticmethod
    def field_keys_for(nodes: list[dict]) -> list[str]:
        """Concrete field keys implied by a path's nodes."""
        keys: list[str] = []
        for node in nodes:
            if node["node"] == "field_key":
                keys = [node["value"]]
            elif node["node"] == "field" and not keys:
                keys = list(taxonomy.FIELD_FAMILIES.get(node["value"], ()))
        return keys


# ------------------------------------------------------------------------ path score
def path_score(nodes: list[dict]) -> float:
    """Geometric mean of the semantic node probabilities (see the module docstring).

    Returns 1.0 for a path with no uncertain node: nothing was guessed, so there is no
    uncertainty to penalise. Probabilities are floored at a small epsilon so one zero
    cannot make ``log`` undefined; the floor is far below any actionable value.
    """
    probabilities = [max(float(node.get("probability") or 0.0), 1e-6) for node in nodes]
    if not probabilities:
        return 1.0
    return round(math.exp(sum(math.log(value) for value in probabilities) / len(probabilities)), 4)


def merge_candidates(path_results: list[tuple[RoutePath, list[dict]]]) -> list[dict]:
    """Fuse per-branch candidate lists into one ranked list.

    Ranking uses weighted reciprocal rank fusion: a row's score is
    ``Σ_branch score_branch / (RRF_K + rank_in_branch)``, where ``score_branch`` is the
    route's path score (the geometric mean of its semantic confidences). That weighting is
    what stops the always-present unconstrained safety path from dominating: it has the most
    candidates, so on a plain merge it would win almost every tie. A row found at rank 1 by a
    confident reading therefore outranks a row found at rank 1 only by the safety net.

    ``RRF_K`` matches the constant the retrieval kernel already uses for BM25+dense fusion
    (see ``engine.Retriever.search``), so both fusions in this pipeline damp ranks the same
    way. Ties break deterministically — best branch rank, then id — because the evaluation
    compares runs against each other.

    Each row keeps how many branches found it and which they were, which is what the UI draws
    as "Branch A · Branch B → merge", and every path records its candidate count.
    """
    fused: dict[str, float] = {}
    best: dict[str, dict] = {}
    for path, hits in path_results:
        path.candidate_count = len(hits)
        weight = max(float(path.score), 1e-6)
        for rank, row in enumerate(hits, start=1):
            fused[row["id"]] = fused.get(row["id"], 0.0) + weight / (RRF_K + rank)
            current = best.get(row["id"])
            if current is None:
                merged = dict(row)
                merged["branch_rank"] = rank
                merged["branches"] = [path.label]
                merged["branch_score"] = path.score
                best[row["id"]] = merged
            else:
                current["branches"].append(path.label)
                if rank < current["branch_rank"]:
                    current["branch_rank"] = rank
                    current["branch_score"] = path.score
    for chunk_id, row in best.items():
        row["fusion"] = round(fused.get(chunk_id, 0.0), 6)
    return sorted(best.values(), key=lambda row: (-row["fusion"], row["branch_rank"], row["id"]))


__all__ = [
    "CONFIG",
    "MODE_ACTIVE",
    "MODE_OFF",
    "MODE_SHADOW",
    "ROUTING_MODES",
    "RoutingConfig",
    "SemanticRouter",
    "has_field_signal",
    "merge_candidates",
    "path_score",
    "routing_mode_from_env",
]
