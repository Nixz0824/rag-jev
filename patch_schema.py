"""Shared vocabulary for patch-note chunks: field keys, direction, ids, wording.

Both the ingest scripts and the answering engine import this module, so a change
to a field pattern cannot silently drift between parsing and retrieval.
"""

from __future__ import annotations

import hashlib
import html
import re

# One change line inside the official announcement, e.g. 冷却时间：4 ⇒ 3.5
# Older announcements use a thin arrow (→), newer ones the double arrow (⇒).
ARROW_RE = re.compile(r"[⇒➔→]|->")
CHANGE_LINE_RE = re.compile(r"^(?P<label>[^：:]{1,28})[：:]\s*(?P<old>.+?)\s*(?:⇒|➔|→)\s*(?P<new>.+?)$")
# Ability header such as "Q - 斩钢闪" or "被动 - 浪客之道"
ABILITY_LINE_RE = re.compile(r"^(?P<ability>被动|[QWER]{1,4})\s*[-－—–]\s*(?P<name>.+)$")
# Bracketed subject such as 【岚切】 used in item rewrites
BRACKET_RE = re.compile(r"^【(?P<name>[^】]{1,20})】")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

# Canonical stat keys. Order matters: the first pattern that matches wins, so
# more specific patterns come first (生命回复 before 生命值, 技能急速 before 急速).
FIELDS = (
    ("health_regen", r"生命回复|回血"),
    ("lifesteal", r"生命偷取|全能吸血|吸血"),
    ("cooldown", r"冷却"),
    ("ability_haste", r"技能急速|冷却缩减"),
    ("cast_time", r"蓄力时间|施法间隔|施法时间|攻击施放时间"),
    ("duration", r"持续时间|时长|维持时间|计时器"),
    ("cost", r"消耗|魔力|法力|蓝耗|耗蓝|能量"),
    ("damage", r"伤害|输出"),
    ("attack_damage", r"攻击力|额外ad|基础ad|适应之力"),
    ("ability_power", r"法强|法术强度|ap加成|ap\b"),
    ("attack_speed", r"攻速|攻击速度"),
    ("movement_speed", r"移速|移动速度"),
    ("health", r"生命值|最大生命|血量|基础生命"),
    ("mana", r"法力值|蓝量|魔力值"),
    ("armor", r"护甲"),
    ("magic_resist", r"魔抗|魔法抗性"),
    ("resistances", r"双抗|抗性"),
    ("magic_pen", r"法术穿透|法穿"),
    ("armor_pen", r"护甲穿透|穿甲"),
    ("range", r"射程|施法距离|范围"),
    ("shield", r"护盾"),
    ("heal", r"治疗|回复量"),
    ("crit", r"暴击"),
    ("tenacity", r"韧性|减速抗性"),
    ("slow", r"减速"),
    ("stacks", r"层数|灵魂|贴纸"),
    ("max_rank", r"最大等级"),
    ("price", r"价格|售价|金币|合成费|费用|总花费|配方|合成路线"),
    ("exp", r"经验"),
    ("stolen_stat", r"属性偷取|窃取属性"),
    ("speed", r"(?<!攻击)(?<!移动)(?<!飞行)速度"),
)

MODE_HINTS = (
    ("classic", ("经典模式", "怀旧模式", "经典服")),
    ("aram", ("大乱斗", "海克斯大乱斗")),
    ("arena", ("竞技场", "斗魂")),
)
MODE_ORDER = {"rift": 0, "classic": 1, "aram": 2, "arena": 3, "other": 4}


def clean(text: str) -> str:
    """HTML entity, bullet and whitespace normalisation for one line of text."""
    text = html.unescape(text)
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"^[\s●○•·・*]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def field_key(label: str) -> str | None:
    for key, pattern in FIELDS:
        if re.search(pattern, label, re.I):
            return key
    return None


def numbers(value: str) -> list[float]:
    return [float(match) for match in NUMBER_RE.findall(value.replace(",", ""))]


def direction(old: str, new: str) -> str:
    """buff / nerf / adjust, derived from the first number on each side."""
    before, after = numbers(old), numbers(new)
    if not before or not after:
        return "adjust"
    if after[0] > before[0]:
        return "buff"
    if after[0] < before[0]:
        return "nerf"
    return "adjust"


def chunk_id(patch: str, mode: str, subject: str, ability: str, field: str, old: str, new: str) -> str:
    material = f"{patch}|{mode}|{subject}|{ability}|{field}|{old}|{new}"
    return "p" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:14]


def split_subject(heading: str) -> tuple[str, str]:
    """'疾风剑豪 亚索' -> ('疾风剑豪', '亚索'); a bare name keeps an empty title."""
    cleaned = clean(heading)
    parts = cleaned.split(" ", 1)
    if len(parts) == 2 and 1 <= len(parts[0]) <= 8:
        return parts[0], parts[1]
    return "", cleaned


def detect_mode(text: str) -> str | None:
    for mode, hints in MODE_HINTS:
        if any(hint in text for hint in hints):
            return mode
    return None


def sentence(row: dict) -> str:
    """Deterministic Chinese sentence for one change; the only place wording lives."""
    head = row.get("subject", "")
    if row.get("ability"):
        head += f" {row['ability']}"
        if row.get("ability_name"):
            head += f"（{row['ability_name']}）"
    field = row.get("field", "")
    old, new = row.get("old_value", ""), row.get("new_value", "")
    if not old:
        body = f"{field}为 {new}"
    else:
        verb = {"buff": "提升至", "nerf": "降低至"}.get(row.get("direction", ""), "调整为")
        body = f"{field}由 {old} {verb} {new}"
    mode = "" if row.get("mode") in (None, "rift") else f"（{row.get('mode_label', row['mode'])}）"
    return f"{row.get('patch', '')} 版本{mode}，{head}的{body}。"
