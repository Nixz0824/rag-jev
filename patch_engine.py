"""Version-aware patch-note question answering on top of the retrieval core.

Numbers in an answer are copied from the corpus, never generated. Every answer
carries its patch, its source and (when available) a Jev rerank trace, so a user
can check the claim against the official announcement.
"""

from __future__ import annotations

import collections
import difflib
import json
import logging
import re
import time
from pathlib import Path

from config import PATCH_DATA, RUNTIME
from engine import BLOCKED, GREETING, Retriever, redact
import jev
import routing
from patch_schema import FIELD_LABELS, FIELDS, detect_mode
from query_plan import (
    PATCH_EXPLICIT,
    PATCH_OUTSIDE,
    PATCH_PREVIOUS,
    PATCH_SYSTEM_DEFAULT,
    PATCH_UI_DEFAULT,
    SOURCE_ALIAS,
    SOURCE_DEFAULT,
    SOURCE_DETERMINISTIC,
    SOURCE_NONE,
    SOURCE_SEMANTIC,
    STATUS_AMBIGUOUS,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    QueryPlan,
)

LOG = logging.getLogger("ragjev.patch")

# 26.17 is the announcement number; Data Dragon calls the same patch 16.17.1.
PATCH_RE = re.compile(r"(?<!\d)(\d{1,2})[.．](\d{1,2})(?:[.．]\d+)?(?!\d)")
ABILITY_RE = re.compile(r"(?i)(?<![a-z])([qwer])(?![a-z])")
ULTIMATE_RE = re.compile(r"大招|大绝|终极技能")
PASSIVE_RE = re.compile(r"被动(?!过|了)")
CHANGE_INTENT = re.compile(r"改|变|调整|加强|增强|削弱|上调|下调|版本|多少|数值|现在|公告|更新", re.I)
OVERVIEW_INTENT = re.compile(r"哪些|有哪些|有什么|都有啥|所有|全部|汇总|总结|一览|列表|改动列表|改动|变动|加强|削弱")
AGGREGATE_INTENT = re.compile(r"哪些|有哪些|有什么|所有|全部|汇总|总结|一览|列表|改动列表")
POSITION_INTENT = re.compile(r"(打野|上单|中单|下路|射手|辅助|打野位|adc|sup)")
UNSUPPORTED_INTENT = re.compile(r"皮肤|炫彩|野区|大龙|小龙|地图|模式改动|赛季奖励|通行证|云顶")
OFF_TOPIC_INTENT = re.compile(r"写.{0,10}诗|写诗|讲.{0,4}故事|攻略|出装推荐|怎么上分|胜率|天气|翻译|算命|写代码|做个网页")
OTHER_SERVER_INTENT = re.compile(r"美服|韩服|欧服|日服|台服|国际服|外服|其它服务器|其他服务器")
REWORK_INTENT = re.compile(r"重做|重制|大型更新|remake|重做版")
WHY_INTENT = re.compile(r"为什么|为何|原因|动机|机制|设计|说明|缘由|调整理由|怎么想|出于")
# "历次 / 一共几次 / 从 X 到 Y" — a multi-hop question about one subject over many patches.
ACROSS_INTENT = re.compile(r"历次|历史|一共|总共|合计|改了几次|改动几次|几次改动|演变|变化史|改动史|所有改动|全部改动|都被改|改过几次")
RANGE_INTENT = re.compile(r"从.{0,14}到|至\s*\d{1,2}[.．]|到\s*\d{1,2}[.．]\d{1,2}")
# Words that never are a champion or item name, used when guessing an unknown subject.
STOPWORDS = re.compile(
    r"这个|那个|当前|最新|上个|上一|版本|公告|英雄|装备|物品|技能|被动|模式|经典|大乱斗|竞技场|加强|削弱|调整|改动|"
    r"所有|全部|哪些|什么|怎么|多少|为什么|现在|都|有|的|了|吗|呢|请|帮|我|看看|查|一下|查询|和|与|在|是|会被|被|"
    r"改|变|变化|更新|内容|啥|东西|一共|一共改"
)

DIRECTION_WORDS = {
    "buff": ("加强", "增强", "提升", "上调", "变强"),
    "nerf": ("削弱", "降低", "下调", "被削", "削", "砍"),
}
DIRECTION_LABELS = {"buff": "加强", "nerf": "削弱", "adjust": "调整"}


def patch_sort(value: str) -> int:
    major, minor = value.split(".")[:2]
    return int(major) * 100 + int(minor)


def normalise(value: str) -> str:
    """Case- and punctuation-insensitive form; whitespace is kept as a separator
    so latin names do not glue onto numbers ('Yasuo 26.17' stays two tokens)."""
    value = re.sub(r"[·・'’\-_（）()]", "", value)
    return re.sub(r"\s+", " ", value).strip().lower()


def field_keys(text: str) -> list[str]:
    """Canonical field keys the question mentions, most specific pattern first."""
    return [key for key, pattern in FIELDS if re.search(pattern, text, re.I)]


# Announcements sometimes write the "new value" as an editorial note rather than a number,
# e.g. `伤害增幅所需距离：525 → 未改动` (15.13 奈德丽). The row is real, but the value did not
# change, so it must not be presented as an adjustment or outrank a row that did change.
REMARK_NEW_VALUES = {"未改动", "不变", "无改动", "保持不变", "未变", "未调整"}


def new_value_is_remark(value: str) -> bool:
    """True when the new value is a note such as 未改动 rather than a value."""
    text = (value or "").strip()
    if not text:
        return False
    if text in REMARK_NEW_VALUES:
        return True
    # `150 - 430 (基于等级) (收益率未改动)` still carries a real value, so only treat it as a
    # pure remark when no digits are present at all.
    return not re.search(r"\d", text) and any(term in text for term in REMARK_NEW_VALUES)


class PatchRetriever(Retriever):
    """Retriever pre-configured for the patch-note corpus."""

    def __init__(self, corpus_path=None, embed_fn=None, alias_path=None, patch_map_path=None, thresholds_path=None):
        path = Path(corpus_path) if corpus_path else PATCH_DATA / "knowledge.json"
        self.alias_path = Path(alias_path) if alias_path else PATCH_DATA / "aliases.json"
        self.patch_map_path = Path(patch_map_path) if patch_map_path else PATCH_DATA / "patch_map.json"
        self.thresholds_path = Path(thresholds_path) if thresholds_path else PATCH_DATA / "thresholds.json"
        self.aliases = []
        super().__init__(
            path,
            instruction="Instruct: Retrieve the exact League of Legends patch note change.\nQuery: ",
            model_tag="Qwen3-Embedding-0.6B-Q8_0",
            embed_fn=embed_fn,
        )
        self.patch_map = self._load_json(self.patch_map_path, {})
        self.thresholds = self._load_json(self.thresholds_path, None)
        # Gate thresholds were measured and deliberately not adopted; see docs/门槛校准.md.
        self.calibration = self._load_json(PATCH_DATA / "gate-calibration.json", None)
        # Ability names ("斩钢闪" -> Q) so questions can be asked by skill name.
        self.abilities = self._load_json(PATCH_DATA / "abilities.json", {})
        self.supported = sorted((self.patch_map.get("patches") or {}).keys(), key=patch_sort)
        self.latest = self.supported[-1] if self.supported else ""
        self._build_aliases()

    @staticmethod
    def _load_json(path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _mark_remark_rows(self) -> None:
        """Flag rows whose new value is an announcement note rather than a value.

        Done once at load so `all()`/`search()` consumers see the same flag: the reranker
        demotes these rows and the renderer explains them instead of printing
        "525 → 未改动" as if it were an adjustment.
        """
        self.remark_rows = 0
        for row in self.chunks:
            if row.get("field_key") == "narrative":
                continue
            if new_value_is_remark(row.get("new_value")):
                row["new_value_is_remark"] = True
                self.remark_rows += 1

    def _build_aliases(self) -> None:
        """Longest-first alias list: official name, title, English name, slang.

        Data Dragon has shipped the Chinese name and title in either order, so the
        alias file is keyed by both and every spelling is mapped onto the subject
        that actually appears in the corpus (matched through the English name).
        """
        self._mark_remark_rows()
        canonical: dict[tuple[str, str], str] = {}
        for row in self.chunks:
            if row.get("type") in ("champion", "item") and row.get("subject_en"):
                canonical.setdefault((row["type"], row["subject_en"]), row["subject"])
        values: set[tuple[str, str, str]] = set()
        for subject, meta in self._load_json(self.alias_path, {}).items():
            meta = meta if isinstance(meta, dict) else {"aliases": meta}
            kind = meta.get("kind") or "champion"
            if kind not in ("champion", "item"):
                continue
            english = meta.get("en", "")
            target = canonical.get((kind, english)) or meta.get("primary") or subject
            for alias in [subject, english, meta.get("primary", ""), *meta.get("aliases", [])]:
                if alias:
                    values.add((normalise(alias), target, kind))
        for row in self.chunks:
            if row.get("type") not in ("champion", "item"):
                continue
            values.add((normalise(row["subject"]), row["subject"], row["type"]))
            for alias in row.get("aliases", []):
                if alias:
                    values.add((normalise(alias), row["subject"], row["type"]))
        self.aliases = sorted(values, key=lambda item: len(item[0]), reverse=True)

    def resolve_subject(self, text: str) -> tuple[str | None, str | None]:
        """Pick the subject a question is about.

        Longest alias wins, then the earliest occurrence, then champions over items —
        without the position tie-break, a short item name inside an ability name
        ("被动过载涌动伤害") could outrank the champion the question is about.
        """
        return self.match_subject(text)[:2]

    def match_subject(self, text: str) -> tuple[str | None, str | None, str]:
        """``resolve_subject`` plus the alias that matched, for the query plan's provenance.

        The returned reason is what the UI shows next to ``subject`` ("别名命中 压缩"), so a
        reader can tell an alias resolution from a lexical guess without reading the code.
        """
        value = normalise(text)
        best: tuple[tuple[int, int, int], str, str, str] | None = None
        for alias, subject, kind in self.aliases:
            if not alias or alias not in value:
                continue
            if re.fullmatch("[a-z0-9]+", alias) and not re.search(
                r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", value
            ):
                continue
            rank = (len(alias), -value.find(alias), 1 if kind == "champion" else 0)
            if best is None or rank > best[0]:
                best = (rank, subject, kind, alias)
        if not best:
            return None, None, ""
        return best[1], best[2], f"别名命中「{best[3]}」"

    def subject_shortlist(self, text: str, limit: int = 3) -> list[tuple[str, str]]:
        """Lexically plausible subjects for a text the alias table could not resolve.

        This is the *only* route by which a semantic model may choose an entity, and it is
        deliberately narrow (see ``routing.SemanticRouter._resolve_subject``): candidates
        come from fuzzy matching and from corpus names contained in the question, so the
        model is asked "which of these three did you mean", never "guess among 170 names".
        Only subjects the corpus can actually answer about are offered.
        """
        answerable = {row["subject"] for row in self.chunks if row.get("field_key") != "narrative"}
        if not answerable:
            return []
        value = normalise(text)
        scored: dict[str, tuple[float, str]] = {}
        for alias, subject, _ in self.aliases:
            if subject not in answerable or not alias:
                continue
            # Ratio-based fuzzy match on the alias the user actually typed, plus a
            # containment test for a bare name with a suffix ("薇恩的技能").
            ratio = difflib.SequenceMatcher(None, value, alias).ratio()
            contained = alias in value or (len(value) >= 2 and value in alias)
            if not contained and ratio < 0.6:
                continue
            score = 0.75 + ratio * 0.25 if contained else ratio
            current = scored.get(subject)
            reason = "问题里直接出现" if contained else f"词形相近（{ratio:.2f}）"
            if current is None or score > current[0]:
                scored[subject] = (score, reason)
        ranked = sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))
        return [(subject, reason) for subject, (_, reason) in ranked[:limit]]


def compare_versions(rows_a: list[dict], rows_b: list[dict]) -> list[dict]:
    """Subject-level diff of two patches' structured changes.

    Rows are matched by (ability, field) inside a subject, so a changed value and a
    new/removed field are distinguishable. Narrative rows are ignored. Subjects are
    keyed without the patch, otherwise every row would look new.
    """
    def index(rows) -> dict[tuple[str, str], dict[tuple[str, str], dict]]:
        grouped: dict[tuple[str, str], dict[tuple[str, str], dict]] = collections.defaultdict(dict)
        for row in rows:
            if row.get("field_key") == "narrative":
                continue
            grouped[(row.get("type", ""), row.get("subject", ""))][(row.get("ability", ""), row.get("field", ""))] = row
        return grouped

    left, right = index(rows_a), index(rows_b)
    result = []
    for group in sorted(set(left) | set(right), key=lambda item: (item[0], item[1])):
        kind, subject = group
        fields: list[dict] = []
        for key in sorted(set(left.get(group, {})) | set(right.get(group, {}))):
            row_a, row_b = left.get(group, {}).get(key), right.get(group, {}).get(key)
            _, field = key
            ability = key[0]
            if row_a and row_b:
                status = "same" if row_a.get("new_value") == row_b.get("new_value") else "changed"
                entry = {"ability": ability, "field": field, "status": status,
                         "a": row_a.get("new_value", ""), "b": row_b.get("new_value", "")}
            elif row_b:
                entry = {"ability": ability, "field": field, "status": "added",
                         "a": "", "b": row_b.get("new_value", "")}
            else:
                entry = {"ability": ability, "field": field, "status": "removed",
                         "a": row_a.get("new_value", ""), "b": ""}
            entry["field_key"] = (row_a or row_b).get("field_key", "")
            entry["direction"] = (row_b or row_a).get("direction", "")
            fields.append(entry)
        result.append(
            {
                "subject": subject,
                "type": kind,
                "patch_a": (list(left[group].values())[0]["patch"] if group in left else ""),
                "patch_b": (list(right[group].values())[0]["patch"] if group in right else ""),
                "rows": fields,
                # number of rows that are not identical between the two patches
                "changed": sum(1 for entry in fields if entry["status"] != "same"),
            }
        )
    return result


class PatchEngine:
    """Parse a version-aware question, filter, retrieve (optionally via Jev), render."""

    def __init__(self, retriever: PatchRetriever, use_jev: bool = True, retrieval_mode: str = "hybrid",
                 self_check: bool = False, routing_mode: str | None = None,
                 routing_config: routing.RoutingConfig | None = None):
        self.r = retriever
        self.use_jev = use_jev and jev.available()
        self.retrieval_mode = retrieval_mode
        # One extra Jev call per answer: does the rendered line match its evidence?
        self.self_check = self_check and self.use_jev
        # How Jev may take part in *query understanding*: off (0.13.0 behaviour), shadow
        # (measure only) or active (routed retrieval). Independent of use_jev so the
        # baseline stays reproducible without a key.
        self.routing_mode = routing_mode or routing.routing_mode_from_env()
        self.routing_config = routing_config or routing.CONFIG
        self.feedback_file = RUNTIME / "feedback.jsonl"

    # ------------------------------------------------------------------ sessions

    def new(self, session_id: str) -> dict:
        return {
            "id": session_id,
            "domain": "patch",
            "created_at": int(time.time()),
            "status": "new",
            "question": "",
            "messages": [],
            "evidence": [],
            "query": None,
            "query_plan": None,
            "routing": None,
            "jev": None,
            "self_check": None,
            # Per-phase Jev accounting, so "how much did routing cost" is answerable.
            "jev_phases": jev.empty_phases(),
            "attempts": [],
            "feedback": [],
            "trace": [],
            "cost_usd": 0.0,
        }

    def add(self, session: dict, role: str, text: str, **fields) -> None:
        session["messages"].append({"role": role, "text": redact(text), "time": int(time.time()), **fields})

    # -------------------------------------------------------------------- parsing

    def canonical_patch(self, value: str) -> str | None:
        """Accept any spelling of a patch and return the one this corpus uses.

        The corpus may contain both schemes (15.13 and 25.15 are both real entries),
        so an exact match wins, then the +10 and -10 interpretations in that order.
        """
        major, minor = (int(part) for part in value.split(".")[:2])
        for candidate in (f"{major}.{minor}", f"{major + 10}.{minor}", f"{major - 10}.{minor}"):
            if candidate in self.r.supported:
                return candidate
        return None

    def parse(self, text: str) -> dict:
        """Backwards-compatible plain-dict view of the plan (see :meth:`plan`).

        Kept because every existing call site, test and evaluation reads a plain dict of
        slot values. ``plan()`` is the richer object; this projects it down without a
        second implementation of the parsing rules.
        """
        return self._query_view(self.plan(text))

    @staticmethod
    def _query_view(plan: QueryPlan) -> dict:
        """Project a plan onto the flat dict the renderers and feedback records use."""
        return {
            "patches": list(plan.patches),
            "outside": list(plan.outside),
            # "上个版本" is an explicit request, not a default: only a question that named
            # no version at all counts as defaulted (which is what lets the UI selector
            # apply, and what triggers the "most recent change instead" fallback).
            "defaulted": plan.patch_source == PATCH_SYSTEM_DEFAULT,
            "subject": plan.value("subject"),
            "type": plan.value("type"),
            "ability": plan.value("ability"),
            "ability_from_name": plan.ability_from_name,
            "direction": plan.value("direction"),
            "field_keys": list(plan.get("field_keys").value or []),
            "mode": plan.value("mode"),
            "why": plan.why,
            "span": list(plan.span) if plan.span else None,
            "aggregate": plan.aggregate,
            "overview": plan.overview,
            "intent": plan.value("intent"),
        }

    def plan(self, text: str) -> QueryPlan:
        """Parse deterministically, recording *how* each slot was decided.

        This is the only place the deterministic rules run. Nothing here calls a model:
        every slot it can close is closed with ``source="deterministic"`` or
        ``"alias"``, and those slots are then off-limits to the semantic router. What it
        leaves open — plus the reason it stayed open — is the router's entire input.
        """
        plan = QueryPlan(question=text)

        # ---------------------------------------------------------------- patches
        patches: list[str] = []
        for match in PATCH_RE.finditer(text):
            canonical = self.canonical_patch(f"{match.group(1)}.{match.group(2)}")
            patches.append(canonical or f"{int(match.group(1))}.{int(match.group(2))}")
        patches = list(dict.fromkeys(patches))
        if patches:
            plan.patch_source = PATCH_EXPLICIT
            note = "正则命中显式版本"
        elif re.search("上个版本|上一版本", text) and len(self.r.supported) > 1:
            patches = [self.r.supported[-2]]
            plan.patch_source = PATCH_PREVIOUS
            note = "「上个版本」→ 上一收录版本"
        else:
            patches = [self.r.latest]
            plan.patch_source = PATCH_SYSTEM_DEFAULT
            note = "未写版本，按最新收录版本"
        plan.patches = patches
        plan.outside = [value for value in patches if value not in self.r.supported]
        plan.set_slot("patches", list(patches),
                      SOURCE_DETERMINISTIC if plan.patch_source == PATCH_EXPLICIT else SOURCE_DEFAULT,
                      STATUS_RESOLVED if patches else STATUS_UNRESOLVED,
                      why=note)

        # ---------------------------------------------------------------- subject
        subject, kind, alias_reason = self.r.match_subject(text)
        if subject:
            plan.set_slot("subject", subject, SOURCE_ALIAS, STATUS_RESOLVED, why=alias_reason)
        else:
            plan.set_slot("subject", None, SOURCE_NONE, STATUS_UNRESOLVED, why="别名表未命中")

        # ---------------------------------------------------------------- ability
        ability, ability_from_name, ability_why = self._parse_ability(text, subject)
        if not ability and subject:
            ability, ability_from_name, ability_why = self._parse_ability_from_name(text, subject)
        if ability:
            # An explicit letter / 大招 / 被动 / skill name is a *closed* slot: the router
            # must not re-ask it, which is the "don't turn a certain answer into a probable
            # one" rule stated as code.
            plan.set_slot("ability", ability, SOURCE_DETERMINISTIC, STATUS_RESOLVED, why=ability_why)
            plan.ability_from_name = ability_from_name
        elif subject and self._ambiguous_ability(text, subject):
            plan.set_slot("ability", None, SOURCE_NONE, STATUS_AMBIGUOUS,
                          why="提到技能相关说法但没有唯一技能，交由语义判断")
        else:
            plan.set_slot("ability", None, SOURCE_NONE, STATUS_UNRESOLVED, why="问题没有指明技能")

        # ------------------------------------------------------------ type / mode
        direction = next(
            (key for key, words in DIRECTION_WORDS.items() if any(word in text for word in words)), None
        )
        requested_type = "item" if re.search(r"装备|物品", text) else ("champion" if re.search(r"英雄", text) else kind)
        if requested_type:
            plan.set_slot("type", requested_type,
                          SOURCE_DETERMINISTIC if re.search(r"装备|物品|英雄", text) else SOURCE_ALIAS,
                          STATUS_RESOLVED,
                          why="问题里写了对象类别" if re.search(r"装备|物品|英雄", text) else "由别名表推断类别")
        else:
            plan.set_slot("type", None, SOURCE_NONE, STATUS_UNRESOLVED, why="问题没有指明对象类别")

        mode = detect_mode(text)
        if mode:
            plan.set_slot("mode", mode, SOURCE_DETERMINISTIC, STATUS_RESOLVED, why="模式关键词命中")
        else:
            plan.set_slot("mode", None, SOURCE_NONE, STATUS_UNRESOLVED, why="问题没有指定模式")

        # ---------------------------------------------------------------- field
        keys = field_keys(text)
        if keys:
            plan.set_slot("field_keys", keys, SOURCE_DETERMINISTIC, STATUS_RESOLVED,
                          why="字段规则命中：" + "、".join(FIELD_LABELS.get(key, key) for key in keys))
            plan.set_slot("field", keys[0], SOURCE_DETERMINISTIC, STATUS_RESOLVED,
                          why=f"字段规则命中「{FIELD_LABELS.get(keys[0], keys[0])}」")
        elif routing.has_field_signal(text):
            plan.set_slot("field_keys", [], SOURCE_NONE, STATUS_AMBIGUOUS,
                          why="问题提到某个数值但规则没有唯一字段，交由语义判断")
            plan.set_slot("field", None, SOURCE_NONE, STATUS_AMBIGUOUS,
                          why="有数值意图但没有命中的字段规则")
        else:
            plan.set_slot("field_keys", [], SOURCE_NONE, STATUS_UNRESOLVED, why="问题没有指明字段")
            plan.set_slot("field", None, SOURCE_NONE, STATUS_UNRESOLVED, why="问题没有指明字段")

        plan.set_slot("direction", direction,
                      SOURCE_DETERMINISTIC if direction else SOURCE_NONE,
                      STATUS_RESOLVED if direction else STATUS_UNRESOLVED,
                      why="方向词命中" if direction else "没有方向说法")

        # --------------------------------------------------- span / aggregate / intent
        supported = [value for value in patches if value in self.r.supported]
        span = None
        if len(supported) >= 2 and (RANGE_INTENT.search(text) or ACROSS_INTENT.search(text)):
            start, end = sorted(supported, key=patch_sort)[0], sorted(supported, key=patch_sort)[-1]
            span = [value for value in self.r.supported if patch_sort(start) <= patch_sort(value) <= patch_sort(end)]
        aggregate = bool(subject) and (
            bool(ACROSS_INTENT.search(text)) or (span is not None and bool(RANGE_INTENT.search(text)))
        )
        if aggregate:
            span = span or list(self.r.supported)
            plan.patches = span
            plan.set_slot("patches", list(span), SOURCE_DETERMINISTIC, STATUS_RESOLVED,
                          why=f"跨版本聚合：{span[0]}—{span[-1]}")
        plan.span = span
        plan.aggregate = aggregate
        plan.overview = bool(AGGREGATE_INTENT.search(text)) or self._bare_aggregate(text)
        plan.why = bool(WHY_INTENT.search(text))

        intent, intent_why = self._parse_intent(plan, text)
        plan.set_slot("intent", intent,
                      SOURCE_DETERMINISTIC if intent else SOURCE_NONE,
                      STATUS_RESOLVED if intent else STATUS_AMBIGUOUS,
                      why=intent_why or "没有命中意图规则，交由语义判断")
        plan.refresh_unresolved()
        return plan

    @staticmethod
    def _parse_ability(text: str, subject: str | None) -> tuple[str | None, str, str]:
        """Deterministic ability resolution: explicit letter → 大招 → 被动.

        Skill *names* are handled separately (see ``_parse_ability_from_name``) because
        they need the resolved subject before they can be looked up.
        """
        match = ABILITY_RE.search(text)
        if match:
            return match.group(1).upper(), "", f"显式技能字母 {match.group(1).upper()}"
        if ULTIMATE_RE.search(text):
            return "R", "", "「大招」规则"
        if PASSIVE_RE.search(text):
            return "被动", "", "「被动」规则"
        return None, "", ""

    def _parse_ability_from_name(self, text: str, subject: str) -> tuple[str | None, str, str]:
        """Match a skill *name* ("斩钢闪") to its slot; players name skills often."""
        names = self.r.abilities.get(subject) or {}
        for slot, name in sorted(names.items(), key=lambda item: -len(item[1])):
            if name and name in text:
                return slot, name, f"技能名命中「{name}」"
        return None, "", ""

    def _ambiguous_ability(self, text: str, subject: str) -> bool:
        """Skill-related wording that does not resolve to one slot.

        Answers "is there a real ability question here" so the router only spends a model
        call when the text actually gestures at a skill (a truncated skill name, or the word
        「技能」) rather than on every question that happens to lack a letter.
        """
        names = self.r.abilities.get(subject) or {}
        for name in names.values():
            if not name:
                continue
            for size in (len(name) - 1, len(name) - 2):
                if size >= 2 and name[:size] in text:
                    return True
        return bool(re.search(r"技能|招|连招|combo", text, re.I))

    @staticmethod
    def _parse_intent(plan: QueryPlan, text: str) -> tuple[str | None, str]:
        """Intent, but only when a deterministic rule actually settles it.

        Leaving it ``None`` is the honest answer for "26.17 亚索改了什么"-shaped questions
        where overview-ness is decided by downstream code, not by a keyword: the router is
        then allowed to ask, and the deterministic branches keep working if it declines.
        """
        if plan.aggregate:
            return "cross_patch_history", "区间/历次说法命中"
        if plan.why:
            return "explanation", "「为什么/原因」命中"
        if plan.value("direction") and not plan.overview:
            return "direction_claim", "方向词命中且非列表问法"
        if plan.overview:
            return "patch_overview" if not plan.value("subject") else "subject_overview", "列表问法命中"
        if plan.value("subject") and re.search(r"改了什么|改了啥|改动|变动|怎么样|如何", text):
            return "subject_overview", "「改了什么」说法命中"
        if plan.value("subject"):
            return "single_fact", "有对象且没有总览说法，按单点数值理解"
        return None, ""

    @staticmethod
    def _bare_aggregate(text: str) -> bool:
        """'26.17改了什么' with nothing else in it still asks for the whole patch."""
        stripped = STOPWORDS.sub("", PATCH_RE.sub("", text))
        stripped = re.sub(r"[\s?？。，,.!！~～\-+也]", "", stripped)
        return not stripped

    @staticmethod
    def _unresolved_token(text: str) -> str:
        """A short Chinese token the alias table did not recognise, if any."""
        stripped = STOPWORDS.sub(" ", PATCH_RE.sub(" ", text))
        candidates = re.findall(r"[\u4e00-\u9fff]{2,4}", stripped)
        return candidates[0] if candidates else ""

    # --------------------------------------------------------------------- chat

    def chat(self, session: dict, text: str, selected_patch: str | None = None) -> dict:
        text = redact(text.strip())
        if not text:
            raise ValueError("请先输入问题")
        if len(text) > 1000:
            raise ValueError("单次输入请控制在1000字以内")
        self.add(session, "user", text)

        if GREETING.fullmatch(text):
            self.add(
                session,
                "assistant",
                "你好。可以直接问版本改动，例如“26.17 亚索改了什么”“26.17 有哪些英雄被削弱”，"
                "不写版本时我按最新收录版本回答并在回答里注明。",
                kind="greeting",
            )
            return session

        if BLOCKED.search(text):
            session["status"] = "abstained"
            self.add(session, "assistant", "这条请求超出我的范围：我只回答版本公告内容，不输出凭据。", kind="abstention")
            return session

        plan = self.plan(text)
        if selected_patch and selected_patch in self.r.supported and plan.patch_source == PATCH_SYSTEM_DEFAULT:
            # The UI version selector only applies when the question itself did not
            # name a version; an explicit version in the text always wins.
            plan.patches = [selected_patch]
            plan.patch_source = PATCH_UI_DEFAULT
            plan.set_slot("patches", [selected_patch], SOURCE_DEFAULT, STATUS_RESOLVED,
                          why="页面版本选择器（问题本身没写版本）")
        plan.outside = [value for value in plan.patches if value not in self.r.supported]
        session["question"] = text
        session["trace"].append({"tool": "版本解析", "detail": ", ".join(plan.patches) or "无"})

        if plan.outside:
            session["status"] = "abstained"
            session["evidence"] = []
            session["query_plan"] = plan.to_json()
            self.add(
                session,
                "assistant",
                f"没有收录版本 {', '.join(plan.outside)} 的更新公告。当前覆盖 "
                f"{self.r.supported[0]}—{self.r.supported[-1]}（共 {len(self.r.supported)} 个版本），"
                f"最新版本是 {self.r.latest}。",
                kind="abstention",
            )
            return session

        if UNSUPPORTED_INTENT.search(text):
            session["status"] = "abstained"
            session["evidence"] = []
            session["query_plan"] = plan.to_json()
            self.add(
                session,
                "assistant",
                "当前语料只收录公告里的英雄、装备与系统数值改动，皮肤/炫彩、云顶之弈等内容不在覆盖范围内。",
                kind="abstention",
            )
            return session

        if REWORK_INTENT.search(text):
            session["status"] = "abstained"
            session["evidence"] = []
            session["query_plan"] = plan.to_json()
            self.add(
                session,
                "assistant",
                "重做/大型更新类公告用描述性文字发布新技能组，不是「旧值 ⇒ 新值」的改动，当前结构化语料不收录。"
                "可以问这个版本其它对象的改动，例如“26.6 有哪些英雄被削弱”。",
                kind="abstention",
            )
            return session

        if OTHER_SERVER_INTENT.search(text):
            session["status"] = "abstained"
            session["evidence"] = []
            session["query_plan"] = plan.to_json()
            self.add(
                session,
                "assistant",
                "我只收录国服公告的数值，不能断言其它服务器的数值是否相同。"
                "可以问国服这个版本的改动，例如“26.17 亚索改了什么”。",
                kind="abstention",
            )
            return session

        if OFF_TOPIC_INTENT.search(text):
            session["status"] = "abstained"
            session["evidence"] = []
            session["query_plan"] = plan.to_json()
            self.add(
                session,
                "assistant",
                "我只回答版本公告里的改动数值，不做攻略、出装推荐或写作。"
                "可以问“26.17 亚索改了什么”这类问题。",
                kind="abstention",
            )
            return session

        if POSITION_INTENT.search(text) and not plan.value("subject"):
            session["status"] = "abstained"
            session["evidence"] = []
            session["query_plan"] = plan.to_json()
            self.add(
                session,
                "assistant",
                "公告不标注英雄位置，我不能可靠回答“哪些打野/上单被改动”。"
                "可以改问具体英雄，或问这个版本的全部改动。",
                kind="abstention",
            )
            return session

        # ---------------------------------------------------- semantic understanding
        # Everything above is deterministic scope handling. Only now, with the question
        # known to be in scope, may a model take part — and only for slots the parser
        # left open (see routing.SemanticRouter).
        plan = self._route(session, plan)

        # A resolved subject already identifies the object; filtering by type as well would
        # hide rows the announcement filed under another section (arena items, for example).
        subject = plan.value("subject")
        ability = plan.value("ability")
        mode = plan.value("mode")
        direction = plan.value("direction")
        where = {key: value for key, value in (("subject", subject), ("ability", ability)) if value}
        if not subject and plan.value("type"):
            where["type"] = plan.value("type")
        where["patches"] = plan.patches
        if mode:
            where["mode"] = mode
        if not subject and direction:
            # A direction word in a listing question is a filter; in a subject
            # question it is a claim to check, so both directions must be visible.
            where["direction"] = direction

        session["query_plan"] = plan.to_json()
        query = self._query_view(plan)
        session["query"] = query

        if plan.aggregate:
            return self._answer_aggregate(session, query)
        if subject and not plan.overview:
            return self._answer_subject(session, text, query, where, plan=plan)
        if subject and plan.overview and OVERVIEW_INTENT.search(text):
            return self._answer_subject(session, text, query, where, plan=plan)
        if not subject and not plan.overview:
            return self._clarify(session, text, query, where)
        return self._answer_overview(session, text, query, where)

    # ------------------------------------------------------------------- routing

    def _route(self, session: dict, plan: QueryPlan) -> QueryPlan:
        """Run the semantic router for this plan and record its outcome in the session.

        ``shadow`` mode deliberately computes everything and then throws the retrieval
        effect away: the plan it returns is the deterministic one, while
        ``session["routing"]`` keeps what the router *would* have done. That is what makes
        an off/shadow/active comparison honest — shadow costs the same calls as active, so
        any quality difference is attributable to using the routing, not to running it.
        """
        if self.routing_mode == routing.MODE_OFF and not self.use_jev:
            # Even with a model configured away, the router runs in its "off" form so the
            # reason is recorded: a phase that did nothing must still say why.
            routing.SemanticRouter(self.r, config=self.routing_config, mode=routing.MODE_OFF).plan(plan)
            session["routing"] = self._routing_payload(plan, called=False)
            for note in plan.routing_notes:
                session["trace"].append({"tool": "语义路由", "detail": note})
            return plan
        router = routing.SemanticRouter(
            self.r, config=self.routing_config, mode=self.routing_mode,
        )
        baseline = {name: plan.get(name).value for name in ("ability", "field", "field_keys", "intent")}
        shadow_mode = self.routing_mode == routing.MODE_SHADOW
        try:
            routed = router.plan(plan, self.r.subject_shortlist(plan.question))
        except Exception as error:  # noqa: BLE001 - routing must never break an answer
            LOG.warning("semantic routing failed: %s", error)
            session["trace"].append({"tool": "语义路由", "detail": f"异常，已退回确定性流程：{error}"})
            session["routing"] = self._routing_payload(plan, called=False)
            return plan
        self._merge_phases(session, router.phases)
        session["cost_usd"] = round(session.get("cost_usd", 0.0) + router.cost_usd, 6)
        if router.beam_notes:
            routed.routing_notes.extend(router.beam_notes)

        if shadow_mode:
            for name, value in baseline.items():
                if routed.get(name).value != value:
                    routed.get(name).value = value
                    routed.get(name).source = SOURCE_DETERMINISTIC if value is not None else SOURCE_NONE
                    routed.get(name).why = "shadow 模式：记录但未采纳"
            routed.routing_notes.append("shadow：语义结果已记录，未影响实际检索")
            session["routing"] = self._routing_payload(routed, called=router.calls > 0)
            for note in routed.routing_notes:
                session["trace"].append({"tool": "语义路由（shadow）", "detail": note})
            return routed

        session["routing"] = self._routing_payload(routed, called=router.calls > 0)
        for note in routed.routing_notes:
            session["trace"].append({"tool": "语义路由", "detail": note})
        return routed

    def _routing_payload(self, plan: QueryPlan, called: bool) -> dict:
        payload = plan.to_json()["routing"]
        payload["called"] = bool(called)
        payload["config"] = self.routing_config.as_json()
        return payload

    @staticmethod
    def _merge_phases(session: dict, phases: dict) -> None:
        """Fold a router's phase ledger into the session's, keeping the worse status."""
        ledger = session.setdefault("jev_phases", jev.empty_phases())
        order = {jev.PHASE_BYPASSED: 0, jev.PHASE_CALLED: 1, jev.PHASE_FALLBACK: 2, jev.PHASE_FAILED: 3}
        for phase, entry in (phases or {}).items():
            current = ledger.setdefault(phase, dict(entry))
            current["calls"] = current.get("calls", 0) + entry.get("calls", 0)
            current["cost_usd"] = round(current.get("cost_usd", 0.0) + entry.get("cost_usd", 0.0), 6)
            current["latency_ms"] = current.get("latency_ms", 0) + entry.get("latency_ms", 0)
            if entry.get("detail"):
                current["detail"] = entry["detail"]
            if order.get(entry.get("status"), 0) > order.get(current.get("status"), 0):
                current["status"] = entry.get("status")

    def _clarify(self, session: dict, text: str, query: dict, where: dict) -> dict:
        token = self._unresolved_token(text)
        known = self.r.all({"patches": query["patches"]})
        rows = [row for row in known if row.get("field_key") != "narrative"]
        champions = list(dict.fromkeys(row["subject"] for row in rows if row["type"] == "champion"))
        items = list(dict.fromkeys(row["subject"] for row in rows if row["type"] == "item"))
        guess = self._closest_subject(token)
        session["status"] = "clarifying"
        session["evidence"] = []
        if guess:
            lines = [f"没认出「{token}」。你是不是想问「{guess}」？可以直接说「{guess}改了什么」。"]
        else:
            lines = [(f"没认出「{token}」是哪个英雄或装备。" if token else "请补充英雄或装备名称。")]
        if champions:
            lines.append(f"这个版本（{', '.join(query['patches'])}）收录的英雄例如：" + "、".join(champions[:6]) + "。")
        if items:
            lines.append("装备例如：" + "、".join(items[:4]) + "（官方名与常见俗称都可以，例如「电刀」=斯塔缇克电刃）。")
        else:
            lines.append("装备请用官方名或俗称，例如「岚切」「无尽之刃」「电刀」。")
        lines.append("也可以问“26.17 有哪些改动”查看全量列表。")
        self.add(session, "assistant", "\n".join(lines), kind="clarification")
        session["trace"].append({"tool": "追问对象名称", "detail": token or "未识别出名称"})
        return session

    def _closest_subject(self, token: str) -> str:
        """A fuzzy "did you mean" that only suggests objects the corpus can answer about."""
        if not token or len(token) < 2:
            return ""
        value = normalise(token)
        pool: dict[str, str] = {}
        answerable = {row["subject"] for row in self.r.chunks if row.get("field_key") != "narrative"}
        for alias, subject, _ in self.r.aliases:
            if subject in answerable:
                pool.setdefault(alias, subject)
        for subject in answerable:
            pool.setdefault(normalise(subject), subject)
        for match in difflib.get_close_matches(value, list(pool), n=1, cutoff=0.72):
            return pool[match]
        contained = [subject for subject in answerable if value in normalise(subject) or normalise(subject) in value]
        return contained[0] if contained else ""

    # ------------------------------------------------------------------- answers

    @staticmethod
    def _prefer_mode(rows: list[dict], mode: str | None) -> list[dict]:
        """Rift changes win by default; another mode only when it is all we have."""
        if mode or not rows:
            return rows
        rift = [row for row in rows if row.get("mode") == "rift"]
        return rift or rows

    def _answer_aggregate(self, session: dict, query: dict) -> dict:
        """One subject across many patches: a timeline with counts, not a single row.

        Patch notes only record changes, so this is a history of adjustments — the
        answer says so instead of pretending it is the current value.
        """
        span = query.get("span") or self.r.supported
        all_rows = [row for row in self.r.all({"subject": query["subject"]}) if row.get("field_key") != "narrative"]
        all_rows = [row for row in all_rows if patch_sort(span[0]) <= patch_sort(row["patch"]) <= patch_sort(span[-1])]
        mode_rows = self._prefer_mode(all_rows, query.get("mode"))
        rows = mode_rows
        narrowed = []
        if query.get("ability"):
            narrowed = [row for row in rows if row.get("ability") == query["ability"]]
        if query["field_keys"]:
            narrowed = [row for row in narrowed or rows if row.get("field_key") in query["field_keys"]]
        if narrowed:
            rows = narrowed
        if query.get("direction"):
            filtered = [row for row in rows if row.get("direction") == query["direction"]]
            if filtered:
                rows = filtered
            else:
                word = "加强" if query["direction"] == "buff" else "削弱"
                session["status"] = "verify"
                session["evidence"] = rows[:12]
                lines = [
                    f"{query['subject']} 在 {span[0]}—{span[-1]} 之间没有{word}记录。",
                    f"同一区间的全部改动共 {len(rows)} 条：",
                ]
                lines += [f"- {row['patch']}：{self._render(row, with_patch=False)}" for row in rows[:8]]
                lines.append("公告只记录改动，这份列表是历次调整的汇总，不等于当前实际数值。")
                self.add(session, "assistant", "\n".join(lines), kind="aggregate", facts=rows[:12])
                session["trace"].append({"tool": "跨版本聚合", "detail": f"没有{word}记录，列出全部 {len(rows)} 条"})
                return session
        if not rows:
            return self._no_result(session, query)

        by_patch: dict[str, list[dict]] = collections.defaultdict(list)
        for row in rows:
            by_patch[row["patch"]].append(row)
        directions = collections.Counter(row["direction"] for row in rows)
        fields = collections.Counter(row["field"] for row in rows).most_common(5)
        head = query["subject"] + (f" {query['ability']}" if query.get("ability") else "")
        direction_word = DIRECTION_LABELS.get(query["direction"], "") if query.get("direction") else ""

        # Show at least one row per patch, then fill up to the budget, so a long
        # history never hides the earliest or the latest version.
        ordered_patches = sorted(by_patch, key=patch_sort)
        shown: dict[str, list[dict]] = {patch: [by_patch[patch][0]] for patch in ordered_patches}
        position = 1
        while sum(len(items) for items in shown.values()) < 18:
            added = False
            for patch in ordered_patches:
                if len(by_patch[patch]) > position:
                    shown[patch].append(by_patch[patch][position])
                    added = True
                    if sum(len(items) for items in shown.values()) >= 18:
                        break
            if not added:
                break
            position += 1

        lines = [
            (
                f"{head} 在 {span[0]}—{span[-1]} 之间共有 {len(rows)} 条{direction_word}记录，跨 {len(by_patch)} 个版本。"
                if direction_word
                else f"{head} 在 {span[0]}—{span[-1]} 之间共被改动 {len(rows)} 次，跨 {len(by_patch)} 个版本。"
            ),
            "",
        ]
        total_shown = 0
        for patch in ordered_patches:
            for row in shown[patch]:
                lines.append(f"- {patch}：{self._render(row, with_patch=False)}")
                total_shown += 1
            hidden = len(by_patch[patch]) - len(shown[patch])
            if hidden > 0:
                lines.append(f"  · {patch} 另有 {hidden} 条未列出")
        if len(rows) > total_shown:
            lines.append(f"…共 {len(rows)} 条，这里列出 {total_shown} 条。")
        dropped = [row for row in all_rows if row not in mode_rows]
        if dropped:
            modes = "、".join(sorted({row.get("mode_label", row.get("mode", "")) for row in dropped}))
            patches = "、".join(sorted({row["patch"] for row in dropped}, key=patch_sort))
            lines.append(
                f"（另有 {len(dropped)} 条来自其它模式：{modes}（{patches}），可用「{modes.split('、')[0]}」再问。）"
            )
        lines += [
            "",
            f"方向：加强 {directions['buff']} 项、削弱 {directions['nerf']} 项、调整 {directions['adjust']} 项。",
            "最常被调整的字段：" + "、".join(f"{name}（{count} 次）" for name, count in fields),
            "来源：" + "、".join(sorted({row["source_title"] for row in rows})[:3])
            + ("等" if len({row["source_title"] for row in rows}) > 3 else ""),
            "公告只记录改动，这份列表是历次调整的汇总，不等于当前实际数值。",
        ]
        session["status"] = "verify"
        session["evidence"] = rows[:30]
        self.add(session, "assistant", "\n".join(lines), kind="aggregate", facts=rows[:30])
        session["trace"].append(
            {"tool": "跨版本聚合", "detail": f"{span[0]}—{span[-1]} 共 {len(rows)} 条，跨 {len(by_patch)} 个版本"}
        )
        return session

    def _recent_change(self, session: dict, query: dict) -> dict:
        """No version given and the latest patch has nothing: show the last change instead.

        Patch notes only record changes, so "现在多少" without a version can only be
        answered as "the most recent change was ...", and the answer says exactly that.
        """
        rows = [row for row in self.r.all({"subject": query["subject"]}) if row.get("field_key") != "narrative"]
        rows = self._prefer_mode(rows, query.get("mode"))
        if query.get("ability"):
            rows = [row for row in rows if row.get("ability") == query["ability"]] or rows
        if query["field_keys"]:
            rows = [row for row in rows if row.get("field_key") in query["field_keys"]] or rows
        if not rows:
            return self._no_result(session, query)
        newest = max(patch_sort(row["patch"]) for row in rows)
        latest_rows = sorted((row for row in rows if patch_sort(row["patch"]) == newest), key=lambda row: row["subject"])
        patch = latest_rows[0]["patch"]
        history = sorted({row["patch"] for row in rows}, key=patch_sort)
        session["status"] = "verify"
        session["evidence"] = latest_rows[:12]
        lines = [
            f"你没有指定版本，而最新收录版本 {self.r.latest} 里没有{query['subject']}的这条改动。",
            f"最近一次改动是 {patch}（历史版本）：",
        ]
        lines += ["- " + self._render(row, with_patch=False) for row in latest_rows[:4]]
        if len(history) > 1:
            lines.append(f"{query['subject']}在收录范围内的改动版本：{'、'.join(history)}（可指定其中一个再问）。")
        lines.append("来源：" + latest_rows[0]["source_title"] + "｜" + latest_rows[0]["source_url"])
        lines.append("公告只记录改动，因此这只是最近一次调整，不等于当前实际数值。")
        self.add(session, "assistant", "\n".join(lines), kind="recent_change", facts=rows[:12])
        session["trace"].append(
            {"tool": "版本回退", "detail": f"最新版没有记录，回退到 {patch} 的最近改动（共 {len(history)} 个版本有记录）"}
        )
        return session

    def _branch_search(self, session: dict, text: str, where: dict, plan: QueryPlan | None) -> list[dict]:
        """Retrieve over the routed paths and merge, or do one plain search.

        With routing off (or no paths) this is exactly the 0.13.0 call. With routing on it
        runs each branch, merges by best branch rank, and records per-branch candidate
        counts on the plan so the UI can draw the fork and what each side produced.
        """
        paths = list(plan.route_paths) if plan else []
        active = [path for path in paths if path.source != SOURCE_DETERMINISTIC or not where.get("field_keys")]
        if self.routing_mode != routing.MODE_ACTIVE or len(paths) < 2:
            hits = self.r.search(text, mode=self.retrieval_mode, k=len(self.r.chunks), where=where)
            if paths:
                paths[0].candidate_count = len(hits)
                paths[0].contributed = bool(hits)
            return hits

        results: list[tuple[routing.RoutePath, list[dict]]] = []
        for path in active:
            branch_where = {**path.where}
            hits = self.r.search(text, mode=self.retrieval_mode, k=len(self.r.chunks), where=branch_where)
            results.append((path, hits))
        if not results:
            return self.r.search(text, mode=self.retrieval_mode, k=len(self.r.chunks), where=where)
        merged = routing.merge_candidates(results)
        # The branch candidate counts and contributed flags only exist now, so both payloads
        # have to be re-serialised after retrieval rather than at plan time. The merged count
        # is reported too: the UI otherwise has to infer it from the trace text.
        if plan is not None:
            session["routing"] = self._routing_payload(plan, called=True)
            session["routing"]["merged_candidates"] = len(merged)
            session["query_plan"] = plan.to_json()
            session["query_plan"]["routing"]["merged_candidates"] = len(merged)
        session["trace"].append(
            {
                "tool": "分支检索",
                "detail": "；".join(
                    f"{path.label}→{len(hits)} 条" for path, hits in results
                ) + f"；合并去重后 {len(merged)} 条",
            }
        )
        return merged

    def _answer_subject(self, session: dict, text: str, query: dict, where: dict,
                        plan: QueryPlan | None = None) -> dict:
        hits = self._branch_search(session, text, where, plan)
        structured = [row for row in hits if row.get("field_key") != "narrative"]
        notes = [row for row in hits if row.get("field_key") == "narrative"]
        if not structured and notes and not query["field_keys"]:
            # The announcement mentions the subject in prose only: say so instead of
            # presenting the note as if it were a recorded change.
            return self._narrative_only(session, query, notes)
        hits = self._prefer_mode(structured or hits, query.get("mode"))
        if not hits:
            if query.get("defaulted") and query["subject"]:
                # No version given and the latest patch has nothing: show the last change
                # instead of claiming there is no record at all.
                return self._recent_change(session, query)
            return self._no_result(session, query)
        if query["field_keys"]:
            # Soft preference: field matches float to the front, but the rest stay
            # visible so Jev can still pick a row whose label is worded differently.
            preferred = [row for row in hits if row.get("field_key") in query["field_keys"]]
            if not preferred:
                # A *semantically* inferred field is a hint, not a filter: if the reading was
                # wrong the conservative branch already retrieved everything, so abstaining
                # here would turn a recoverable guess into a refusal. Only a field the
                # deterministic rules read out of the question may abstain (0.13.0 behaviour).
                inferred = bool(plan and plan.get("field_keys").source == SOURCE_SEMANTIC)
                if inferred:
                    session["trace"].append(
                        {"tool": "字段核对", "detail": "语义推断的字段在本版本没有记录，保留全部候选继续判断"}
                    )
                elif query.get("defaulted") and query["subject"]:
                    # The asked stat exists, just not in the latest patch.
                    return self._recent_change(session, query)
                else:
                    return self._field_not_found(session, query, hits)
            others = [row for row in hits if row.get("field_key") not in query["field_keys"]]
            hits = preferred + others
        ordered, jev_meta = self._rerank(text, hits)
        session["evidence"] = ordered[:12]
        # Which branch the winner came from: the measurement of whether routing helped.
        if ordered and plan is not None and plan.beam_used:
            winner = ordered[0]
            branches = winner.get("branches") or []
            session["routing_influence"] = {
                "beam_used": True,
                "winner_branches": branches,
                "from_secondary": bool(branches) and branches[0] != (plan.route_paths[0].label if plan.route_paths else ""),
            }
            if plan.route_paths:
                primary_label = plan.route_paths[0].label
                for path in plan.route_paths:
                    path.contributed = path.label in branches
                if branches and branches[0] != primary_label:
                    session["trace"].append(
                        {"tool": "分支合并", "detail": f"最终采用的证据来自次选分支 {branches[0]}，首选分支未命中"}
                    )
        if jev_meta:
            session["cost_usd"] += jev_meta["cost_usd"]
            session["jev"] = {
                "choice_id": jev_meta["choice_id"],
                "latency_ms": jev_meta["latency_ms"],
                "cost_usd": jev_meta["cost_usd"],
                "model": jev_meta["model"],
                "scores": jev_meta["scores"],
                # kept so the UI and the evaluation can show retrieval order vs Jev order
                "retrieval_top": hits[0]["id"] if hits else None,
                "after_top": ordered[0]["id"] if ordered else None,
                "decisive": jev_meta.get("decisive", True),
                "gap": jev_meta.get("gap", 0.0),
                "candidates": len(hits),
            }
            jev.book(session.setdefault("jev_phases", jev.empty_phases()), "rerank", jev.PHASE_CALLED,
                     jev_meta["cost_usd"], jev_meta["latency_ms"],
                     f"{len(hits)} 条候选，首选 {jev_meta['choice_id'] or '无'}", calls=1)
            session["trace"].append(
                {
                    "tool": "Jev 重排",
                    "detail": f"{jev_meta['model']}｜{jev_meta['latency_ms']}ms｜${jev_meta['cost_usd']:.6f}"
                    f"｜首选 {jev_meta['choice_id'] or '无'}"
                    + ("" if jev_meta.get("decisive", True) else f"｜分差 {jev_meta.get('gap', 0):.2f} 未达阈值，保留检索顺序"),
                }
            )
        elif self.use_jev:
            detail = "候选不足 2 条，未调用" if len(hits) < 2 else "调用失败，保留 BM25+向量排序"
            jev.book(session.setdefault("jev_phases", jev.empty_phases()), "rerank",
                     jev.PHASE_BYPASSED if len(hits) < 2 else jev.PHASE_FALLBACK, detail=detail)
            session["trace"].append({"tool": "Jev 重排", "detail": detail})
        elif len(hits) < 2:
            jev.book(session.setdefault("jev_phases", jev.empty_phases()), "rerank",
                     jev.PHASE_BYPASSED, detail="候选不足 2 条，无可重排")
        else:
            # No key (or Jev disabled) with a real candidate set: that is a degradation, and
            # it must be reported as one even though routing (which is independent) ran fine.
            jev.book(session.setdefault("jev_phases", jev.empty_phases()), "rerank",
                     jev.PHASE_FALLBACK, detail="没有 TYPESAFE_API_KEY，未做重排")
        top = ordered[0]
        if query["direction"]:
            claimed = query["direction"]
            same = [row for row in ordered if row.get("direction") == claimed]
            other = [row for row in ordered if row.get("direction") in ("buff", "nerf") and row.get("direction") != claimed]
            claim_word = DIRECTION_LABELS[claimed]
            if same and other:
                # Both directions exist for this subject: saying only one would mislead.
                session["status"] = "verify"
                lines = [
                    self._note(query),
                    f"{query['subject']}在 {', '.join(query['patches'])} 既有{claim_word}也有"
                    f"{DIRECTION_LABELS[other[0]['direction']]}：",
                    f"· {claim_word}：",
                ]
                lines += ["- " + self._render(row, with_patch=False) for row in same[:3]]
                lines.append(f"· {DIRECTION_LABELS[other[0]['direction']]}：")
                lines += ["- " + self._render(row, with_patch=False) for row in other[:3]]
                lines.append("来源：" + same[0]["source_title"] + "｜" + same[0]["source_url"])
                caution = self._self_check_lines(
                    session,
                    [(self._render(row, with_patch=False), self._evidence_for(row)) for row in (same[:3] + other[:3])],
                )
                if caution:
                    lines.append(caution)
                self.add(session, "assistant", "\n".join(lines), kind="claim_check", facts=ordered[:12])
                session["trace"].append(
                    {"tool": "核对断言", "detail": f"用户说{claim_word}，公告两个方向都有（{len(same)}/{len(other)} 条）"}
                )
                return session
            if not same:
                actual = top.get("direction")
                actual_label = DIRECTION_LABELS.get(actual, "调整")
                session["status"] = "verify"
                claim_line = f"{query['subject']}在 {', '.join(query['patches'])} 没有被{claim_word}：公告里是{actual_label}。"
                caution = self._self_check_lines(
                    session,
                    [
                        (claim_line, self._evidence_for(top)),
                        (self._render(top, with_patch=False), self._evidence_for(top)),
                    ],
                )
                self.add(
                    session,
                    "assistant",
                    self._note(query)
                    + "\n"
                    + claim_line
                    + "\n- "
                    + self._render(top, with_patch=False)
                    + ("\n" + caution if caution else "")
                    + "\n来源：" + top["source_title"] + "｜" + top["source_url"],
                    kind="claim_check",
                    facts=ordered[:12],
                )
                session["trace"].append({"tool": "核对断言", "detail": f"用户说{claim_word}，公告是{actual_label}"})
                return session
            ordered = same
            session["evidence"] = ordered[:12]
            top = ordered[0]
        session["status"] = "verify"
        lines = [self._note(query), self._render(top)]
        others = [row for row in ordered[1:6] if row["subject"] == top["subject"] and row["patch"] == top["patch"]]
        if others:
            lines.append("\n同一对象的其他改动：")
            lines += ["- " + self._render(row, with_patch=False) for row in others[:4]]
        lines.append("\n来源：" + top["source_title"] + "｜" + top["source_url"])
        if query.get("why"):
            notes = [
                row
                for row in self.r.all({"patches": query["patches"], "subject": top["subject"]})
                if row.get("field_key") == "narrative" and row.get("mode") == top.get("mode")
            ]
            if notes:
                lines.append("\n公告里的说明：")
                lines += ["- " + note["new_value"][:220] for note in notes[:2]]
        caution = self._self_check_lines(
            session,
            [(self._render(row, with_patch=False), self._evidence_for(row)) for row in [top, *others[:3]]],
        )
        if caution:
            lines.append(caution)
        self.add(session, "assistant", "\n".join(lines), kind="patch_answer", facts=ordered[:12])
        return session

    def _answer_overview(self, session: dict, text: str, query: dict, where: dict) -> dict:
        rows = [row for row in self.r.all(where) if row.get("field_key") != "narrative"]
        rows = self._prefer_mode(rows, query.get("mode"))
        if not rows:
            return self._no_result(session, query)
        grouped = collections.defaultdict(list)
        for row in rows:
            grouped[(row["patch"], row["type"], row["subject"])].append(row)
        ordered = sorted(grouped.items(), key=lambda item: (patch_sort(item[0][0]), item[0][1], item[0][2]))
        lines = [self._note(query)]
        for (patch, kind, subject), changes in ordered[:40]:
            fields = "、".join(dict.fromkeys(row["field"] for row in changes))
            label = f"{subject}（{len(changes)} 项：{fields}）"
            lines.append(("- " + label) if len(ordered) <= 40 else label)
        if len(ordered) > 40:
            lines.append(f"…共 {len(ordered)} 个对象，{sum(len(c) for _, c in ordered)} 条改动（已截断显示）。")
        lines.append(f"\n共 {len(ordered)} 个对象、{sum(len(c) for _, c in ordered)} 条改动。")
        lines.append("来源：" + "、".join(sorted({row["source_title"] for row in rows}))[:200])
        session["status"] = "verify"
        session["evidence"] = rows[:40]
        self.add(session, "assistant", "\n".join(lines), kind="patch_overview", facts=rows[:40])
        return session

    @staticmethod
    def _evidence_for(row: dict) -> str:
        """Corpus sentence plus the structured values, so wording cannot be mistaken
        for a grounding failure while wrong numbers still are."""
        values = f"字段 {row.get('field', '')}：{row.get('old_value', '')} → {row.get('new_value', '')}".strip()
        return f"{row.get('text', '')}｜{values}｜版本 {row.get('patch', '')}"

    def _self_check_lines(self, session: dict, pairs: list[tuple[str, str]]) -> str:
        """Judge rendered lines against their evidence; return a caution line or ''."""
        ledger = session.setdefault("jev_phases", jev.empty_phases())
        if not self.self_check or not pairs:
            jev.book(ledger, "evidence_judge", jev.PHASE_BYPASSED,
                     detail="未开启自检或没有数值行" if not self.self_check else "没有可比对的数值行")
            return ""
        result = jev.judge_many(pairs)
        if not result:
            jev.book(ledger, "evidence_judge", jev.PHASE_FALLBACK, detail="调用失败，跳过自检")
            session["trace"].append({"tool": "回答自检", "detail": "不可用，跳过"})
            return ""
        session["cost_usd"] += result["cost_usd"]
        weak = [index for index, score in enumerate(result["scores"]) if score < 0.5]
        session["self_check"] = {
            "min": result["min"],
            "mean": result["mean"],
            "scores": result["scores"],
            "weak": weak,
        }
        jev.book(ledger, "evidence_judge", jev.PHASE_CALLED, result["cost_usd"], result["latency_ms"],
                 f"{len(result['scores'])} 条断言，最低支持度 {result['min']:.2f}", calls=1)
        session["trace"].append(
            {
                "tool": "回答自检",
                "detail": f"{result['model']}｜{len(result['scores'])} 条｜最低 {result['min']:.2f}"
                f"｜${result['cost_usd']:.6f}"
                + (f"｜弱证据 {weak}" if weak else ""),
            }
        )
        if weak:
            return "（自检：这条回答与证据的一致性偏低，请以公告原文为准。）"
        return ""

    def _rerank(self, text: str, hits: list[dict]) -> tuple[list[dict], dict | None]:
        # Rows whose "new value" is an announcement note (未改动) are never the answer to
        # "改成多少了": they did not change. They stay in the candidate list — the claim is
        # part of the patch — but only as the last resort.
        changes = [row for row in hits if not row.get("new_value_is_remark")]
        remarks = [row for row in hits if row.get("new_value_is_remark")]
        if remarks and not changes:
            return hits, None
        if not self.use_jev or len(changes) < 2:
            return changes + remarks, None
        meta = jev.rerank(text, changes[: jev.MAX_CANDIDATES])
        if not meta:
            return changes + remarks, None
        by_id = {row["id"]: row for row in changes}
        ordered = [by_id[row["id"]] for row in meta["ordered"] if row["id"] in by_id]
        ordered += [row for row in changes if row["id"] not in {item["id"] for item in ordered}]
        return ordered + remarks, meta

    # ------------------------------------------------------------------ rendering

    def _note(self, query: dict) -> str:
        prefix = "你没有指定版本；" if query["defaulted"] else ""
        label = "最新收录版本" if query["patches"] and query["patches"][-1] == self.r.latest else "历史版本"
        return f"{prefix}按{label} {', '.join(query['patches'])} 的国服公告回答。"

    @staticmethod
    def _render(row: dict, with_patch: bool = True) -> str:
        head = f"{row['subject']}"
        if row.get("ability"):
            head += f" {row['ability']}"
            if row.get("ability_name"):
                head += f"（{row['ability_name']}）"
        change = f"{row['field']}：{row['old_value']} → {row['new_value']}" if row.get("old_value") else (
            f"{row['field']}：{row['new_value']}"
        )
        if row.get("new_value_is_remark"):
            # The announcement wrote something like `525 → 未改动`: the numeric value did NOT
            # change, and the "new value" is an editorial note. Saying so beats rendering
            # "525 → 未改动" as though it were an adjustment.
            change = f"{row['field']}：{row['old_value']}（公告注明{row['new_value']}，数值未变）" \
                if row.get("old_value") else f"{row['field']}（{row['new_value']}）"
        return f"{head} {change}" + (f"（{row['patch']}）" if with_patch else "")

    def _narrative_only(self, session: dict, query: dict, notes: list[dict]) -> dict:
        """Only prose exists for this subject and patch — no numeric change to render."""
        head = query["subject"] + (f" {query['ability']}" if query.get("ability") else "")
        session["status"] = "abstained"
        session["evidence"] = notes[:6]
        lines = [
            self._note(query),
            f"公告里没有{head}的数值改动条目，只有说明文字：",
        ]
        lines += ["- " + note["new_value"][:220] for note in notes[:2]]
        lines.append("说明文字不构成改动记录，所以这里不给数值。")
        lines.append("来源：" + notes[0]["source_title"] + "｜" + notes[0]["source_url"])
        self.add(session, "assistant", "\n".join(lines), kind="abstention")
        session["trace"].append({"tool": "证据核对", "detail": "只有说明文字，没有数值改动条目"})
        return session

    def _field_not_found(self, session: dict, query: dict, hits: list[dict]) -> dict:
        """The user named a stat that this patch simply does not touch."""
        labels = "、".join(FIELD_LABELS.get(key, key) for key in query["field_keys"])
        head = query["subject"] + (f" {query['ability']}" if query.get("ability") else "")
        session["status"] = "abstained"
        session["evidence"] = hits[:6]
        lines = [
            self._note(query),
            f"公告里没有{head}的{labels}改动记录。",
        ]
        if hits:
            lines.append("这个版本收录的是：")
            lines += ["- " + self._render(row, with_patch=False) for row in hits[:5]]
        lines.append("如果改动来自版本内热修（公告之外），当前语料不包含。")
        self.add(session, "assistant", "\n".join(lines), kind="abstention")
        session["trace"].append({"tool": "字段核对", "detail": f"没有 {labels} 记录，给出实际收录项"})
        return session

    def _no_result(self, session: dict, query: dict) -> dict:
        subject = query.get("subject") or "该条件"
        target = subject + (f" {query['ability']}" if query.get("ability") else "")
        if query.get("field_keys"):
            target += " 的" + "、".join(FIELD_LABELS.get(key, key) for key in query["field_keys"])
        others = []
        if query.get("subject"):
            others = self.r.all({"subject": query["subject"], "patches": query["patches"]})[:6]
        lines = [
            self._note(query),
            f"在 {', '.join(query['patches'])} 的公告里没有找到“{target}”对应的记录。",
        ]
        if others:
            lines.append("该版本里这个对象实际收录的改动：")
            lines += ["- " + self._render(row, with_patch=False) for row in others]
        elif query.get("subject"):
            elsewhere = [row for row in self.r.all({"subject": query["subject"]}) if row.get("field_key") != "narrative"]
            if elsewhere:
                patches = list(dict.fromkeys(row["patch"] for row in elsewhere))[:6]
                lines.append(
                    f"其它版本里有这个对象的记录：{ '、'.join(patches) }。可以问其中某个版本，例如"
                    f"“{patches[-1]} {query['subject']}改了什么”。"
                )
        lines.append("如果这条改动来自版本内热修（公告之外的调整），当前语料不包含。")
        session["status"] = "abstained"
        session["evidence"] = others
        self.add(session, "assistant", "\n".join(lines), kind="abstention")
        return session

    # ------------------------------------------------------------------- feedback

    def feedback(self, session: dict, result: str, note: str = "") -> dict:
        if result not in ("accurate", "incorrect", "outdated"):
            raise ValueError("反馈类型无效")
        if session.get("status") != "verify":
            raise ValueError("当前没有等待准确性反馈的回答")
        record = {
            "session_id": session["id"],
            "question": session.get("question", ""),
            "query": session.get("query"),
            "result": result,
            "note": redact(note[:500]),
            "evidence_ids": [row["id"] for row in session.get("evidence", [])],
            "time": int(time.time()),
        }
        self.feedback_file.parent.mkdir(exist_ok=True)
        with self.feedback_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        session["feedback"].append(record)
        session["status"] = "done" if result == "accurate" else "needs_review"
        labels = {"accurate": "准确", "incorrect": "有误", "outdated": "可能过时"}
        self.add(session, "assistant", f"已记录“{labels[result]}”反馈。", kind="feedback_recorded")
        return session


FIELD_KEY_CHOICES = {key: pattern for key, pattern in FIELDS}
