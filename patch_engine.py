"""Version-aware patch-note question answering on top of the retrieval core.

Numbers in an answer are copied from the corpus, never generated. Every answer
carries its patch, its source and (when available) a Jev rerank trace, so a user
can check the claim against the official announcement.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import time
from pathlib import Path

from config import PATCH_DATA, RUNTIME
from engine import BLOCKED, GREETING, Retriever, redact
import jev
from patch_schema import FIELD_LABELS, FIELDS, detect_mode

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
        self.supported = sorted((self.patch_map.get("patches") or {}).keys(), key=patch_sort)
        self.latest = self.supported[-1] if self.supported else ""
        self._build_aliases()

    @staticmethod
    def _load_json(path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _build_aliases(self) -> None:
        """Longest-first alias list: official name, title, English name, slang.

        Data Dragon has shipped the Chinese name and title in either order, so the
        alias file is keyed by both and every spelling is mapped onto the subject
        that actually appears in the corpus (matched through the English name).
        """
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
        value = normalise(text)
        for alias, subject, kind in self.aliases:
            if not alias or alias not in value:
                continue
            if re.fullmatch("[a-z0-9]+", alias) and not re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", value):
                continue
            return subject, kind
        return None, None


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

    def __init__(self, retriever: PatchRetriever, use_jev: bool = True, retrieval_mode: str = "hybrid", self_check: bool = False):
        self.r = retriever
        self.use_jev = use_jev and jev.available()
        self.retrieval_mode = retrieval_mode
        # One extra Jev call per answer: does the rendered line match its evidence?
        self.self_check = self_check and self.use_jev
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
            "attempts": [],
            "feedback": [],
            "trace": [],
            "cost_usd": 0.0,
        }

    def add(self, session: dict, role: str, text: str, **fields) -> None:
        session["messages"].append({"role": role, "text": redact(text), "time": int(time.time()), **fields})

    # -------------------------------------------------------------------- parsing

    def canonical_patch(self, value: str) -> str | None:
        """Accept 26.17, 16.17 and 16.17.1 and return the announcement number."""
        major, minor = (int(part) for part in value.split(".")[:2])
        if major >= 20:
            candidate = f"{major}.{minor}"
        elif 10 <= major <= 19:
            candidate = f"{major + 10}.{minor}"
        else:
            candidate = f"{major + 20}.{minor}"
        return candidate if candidate in self.r.supported else None

    def parse(self, text: str) -> dict:
        patches = []
        for match in PATCH_RE.finditer(text):
            canonical = self.canonical_patch(f"{match.group(1)}.{match.group(2)}")
            raw = f"{int(match.group(1))}.{int(match.group(2))}"
            patches.append(canonical or raw)
        patches = list(dict.fromkeys(patches))
        subject, kind = self.r.resolve_subject(text)
        ability_match = ABILITY_RE.search(text)
        if ability_match:
            ability = ability_match.group(1).upper()
        elif ULTIMATE_RE.search(text):
            ability = "R"
        elif PASSIVE_RE.search(text):
            ability = "被动"
        else:
            ability = None
        direction = next(
            (key for key, words in DIRECTION_WORDS.items() if any(word in text for word in words)), None
        )
        requested_type = "item" if re.search(r"装备|物品", text) else ("champion" if re.search(r"英雄", text) else kind)
        keys = field_keys(text)
        defaulted = False
        if not patches:
            defaulted = not re.search("当前版本|最新版本|最新版|这个版本|这版本|上个版本|上一版本", text)
            if re.search("上个版本|上一版本", text) and len(self.r.supported) > 1:
                patches = [self.r.supported[-2]]
            else:
                patches = [self.r.latest]
        return {
            "patches": [value for value in patches if value in self.r.supported],
            "outside": [value for value in patches if value not in self.r.supported],
            "defaulted": defaulted,
            "subject": subject,
            "type": requested_type,
            "ability": ability,
            "direction": direction,
            "field_keys": keys,
            "mode": detect_mode(text),
            "overview": bool(AGGREGATE_INTENT.search(text)) or self._bare_aggregate(text),
        }

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

        query = self.parse(text)
        if selected_patch and selected_patch in self.r.supported and query["defaulted"]:
            # The UI version selector only applies when the question itself did not
            # name a version; an explicit version in the text always wins.
            query.update(patches=[selected_patch], defaulted=False)
        session["question"] = text
        session["query"] = query
        session["trace"].append({"tool": "版本解析", "detail": ", ".join(query["patches"]) or "无"})

        if query["outside"]:
            session["status"] = "abstained"
            session["evidence"] = []
            self.add(
                session,
                "assistant",
                f"没有收录版本 {', '.join(query['outside'])} 的更新公告。当前覆盖 "
                f"{self.r.supported[0]}—{self.r.supported[-1]}（共 {len(self.r.supported)} 个版本），"
                f"最新版本是 {self.r.latest}。",
                kind="abstention",
            )
            return session

        if UNSUPPORTED_INTENT.search(text):
            session["status"] = "abstained"
            session["evidence"] = []
            self.add(
                session,
                "assistant",
                "当前语料只收录公告里的英雄、装备与系统数值改动，皮肤/炫彩、云顶之弈等内容不在覆盖范围内。",
                kind="abstention",
            )
            return session

        if OTHER_SERVER_INTENT.search(text):
            session["status"] = "abstained"
            session["evidence"] = []
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
            self.add(
                session,
                "assistant",
                "我只回答版本公告里的改动数值，不做攻略、出装推荐或写作。"
                "可以问“26.17 亚索改了什么”这类问题。",
                kind="abstention",
            )
            return session

        if POSITION_INTENT.search(text) and not query["subject"]:
            session["status"] = "abstained"
            session["evidence"] = []
            self.add(
                session,
                "assistant",
                "公告不标注英雄位置，我不能可靠回答“哪些打野/上单被改动”。"
                "可以改问具体英雄，或问这个版本的全部改动。",
                kind="abstention",
            )
            return session

        where = {key: query[key] for key in ("subject", "type", "ability") if query.get(key)}
        where["patches"] = query["patches"]
        if query.get("mode"):
            where["mode"] = query["mode"]
        if not query["subject"] and query.get("direction"):
            # A direction word in a listing question is a filter; in a subject
            # question it is a claim to check, so both directions must be visible.
            where["direction"] = query["direction"]
        if query["subject"] and not query["overview"]:
            return self._answer_subject(session, text, query, where)
        if query["subject"] and query["overview"] and OVERVIEW_INTENT.search(text):
            return self._answer_subject(session, text, query, where)
        if not query["subject"] and not query["overview"]:
            return self._clarify(session, text, query, where)
        return self._answer_overview(session, text, query, where)

    def _clarify(self, session: dict, text: str, query: dict, where: dict) -> dict:
        token = self._unresolved_token(text)
        known = self.r.all({"patches": query["patches"]})
        subjects = list(dict.fromkeys(row["subject"] for row in known if row.get("field_key") != "narrative"))
        session["status"] = "clarifying"
        session["evidence"] = []
        lines = [
            (f"没认出「{token}」是哪个英雄或装备。" if token else "请补充英雄或装备名称。"),
            f"这个版本（{', '.join(query['patches'])}）收录了 {len(subjects)} 个对象，例如："
            + "、".join(subjects[:8])
            + "。",
            "也可以问“26.17 有哪些改动”查看全量列表。",
        ]
        self.add(session, "assistant", "\n".join(lines), kind="clarification")
        session["trace"].append({"tool": "追问对象名称", "detail": token or "未识别出名称"})
        return session

    # ------------------------------------------------------------------- answers

    @staticmethod
    def _prefer_mode(rows: list[dict], mode: str | None) -> list[dict]:
        """Rift changes win by default; another mode only when it is all we have."""
        if mode or not rows:
            return rows
        rift = [row for row in rows if row.get("mode") == "rift"]
        return rift or rows

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
        rows = sorted((row for row in rows if patch_sort(row["patch"]) == newest), key=lambda row: row["subject"])
        patch = rows[0]["patch"]
        session["status"] = "verify"
        session["evidence"] = rows[:12]
        lines = [
            f"你没有指定版本，而最新收录版本 {self.r.latest} 里没有{query['subject']}的这条改动。",
            f"最近一次改动是 {patch}（历史版本）：",
        ]
        lines += ["- " + self._render(row, with_patch=False) for row in rows[:4]]
        lines.append("来源：" + rows[0]["source_title"] + "｜" + rows[0]["source_url"])
        lines.append("公告只记录改动，因此这只是最近一次调整，不等于当前实际数值。")
        self.add(session, "assistant", "\n".join(lines), kind="recent_change", facts=rows[:12])
        session["trace"].append({"tool": "版本回退", "detail": f"最新版没有记录，回退到 {patch} 的最近改动"})
        return session

    def _answer_subject(self, session: dict, text: str, query: dict, where: dict) -> dict:
        hits = self.r.search(text, mode=self.retrieval_mode, k=len(self.r.chunks), where=where)
        structured = [row for row in hits if row.get("field_key") != "narrative"]
        hits = self._prefer_mode(structured or hits, query.get("mode"))
        if not hits:
            if query.get("defaulted") and query["subject"] and (query["field_keys"] or query.get("ability")):
                return self._recent_change(session, query)
            return self._no_result(session, query)
        if query["field_keys"]:
            # Soft preference: field matches float to the front, but the rest stay
            # visible so Jev can still pick a row whose label is worded differently.
            preferred = [row for row in hits if row.get("field_key") in query["field_keys"]]
            if not preferred:
                if query.get("defaulted") and query["subject"]:
                    # The asked stat exists, just not in the latest patch.
                    return self._recent_change(session, query)
                return self._field_not_found(session, query, hits)
            others = [row for row in hits if row.get("field_key") not in query["field_keys"]]
            hits = preferred + others
        ordered, jev_meta = self._rerank(text, hits)
        session["evidence"] = ordered[:12]
        if jev_meta:
            session["cost_usd"] += jev_meta["cost_usd"]
            session["jev"] = {
                "choice_id": jev_meta["choice_id"],
                "latency_ms": jev_meta["latency_ms"],
                "cost_usd": jev_meta["cost_usd"],
                "model": jev_meta["model"],
                "scores": jev_meta["scores"],
            }
            session["trace"].append(
                {
                    "tool": "Jev 重排",
                    "detail": f"{jev_meta['model']}｜{jev_meta['latency_ms']}ms｜${jev_meta['cost_usd']:.6f}"
                    f"｜首选 {jev_meta['choice_id'] or '无'}",
                }
            )
        elif self.use_jev:
            detail = "候选不足 2 条，未调用" if len(hits) < 2 else "调用失败，保留 BM25+向量排序"
            session["trace"].append({"tool": "Jev 重排", "detail": detail})
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
        if not self.self_check or not pairs:
            return ""
        result = jev.judge_many(pairs)
        if not result:
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
        if not self.use_jev or len(hits) < 2:
            return hits, None
        meta = jev.rerank(text, hits[: jev.MAX_CANDIDATES])
        if not meta:
            return hits, None
        by_id = {row["id"]: row for row in hits}
        ordered = [by_id[row["id"]] for row in meta["ordered"] if row["id"] in by_id]
        ordered += [row for row in hits if row["id"] not in {item["id"] for item in ordered}]
        return ordered, meta

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
        return f"{head} {change}" + (f"（{row['patch']}）" if with_patch else "")

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
