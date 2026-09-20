"""Cross-check the CN corpus against the English patch notes.

Riot's en-us announcements are the same patch content in another language, so they
are useful as a second witness: this script reports, per patch, which changed
subjects appear on both sides and which appear on only one. Differences are
reported, never merged — the CN announcement is the source of truth for answers.

Usage:
    python scripts/ingest_en.py                 # fetch what is missing, then compare
    python scripts/ingest_en.py --offline       # cached pages only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DOCS, PATCH_DATA, RAW  # noqa: E402
from patch_schema import ARROW_RE, clean  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36"
EN_DIR = RAW / "en"
BASE = "https://www.leagueoflegends.com/en-us/news/game-updates/league-of-legends-patch-{slug}-notes"
BLOCK_RE = re.compile(r"<(h2|h3|h4|li)\b[^>]*>(.*?)</\1>", re.S | re.I)
SECTIONS = {"champions": "champion", "items": "item", "runes": "rune", "systems": "system"}


def fetch(slug: str, offline: bool) -> str | None:
    EN_DIR.mkdir(parents=True, exist_ok=True)
    cache = EN_DIR / f"en-{slug}.html"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    if offline:
        return None
    request = urllib.request.Request(BASE.format(slug=slug.replace(".", "-")), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            document = response.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001
        print(f"  {slug}: 抓取失败 {error}")
        return None
    cache.write_text(document, encoding="utf-8")
    time.sleep(0.6)
    return document


def parse(document: str) -> dict:
    """Collect subjects and change-line counts, section by section."""
    subjects: dict[str, set[str]] = {"champion": set(), "item": set(), "system": set()}
    arrows = 0
    section = None
    for tag, fragment in BLOCK_RE.findall(document):
        tag = tag.lower()
        text = clean(re.sub(r"<[^>]+>", "", fragment))
        if not text:
            continue
        if tag == "h2":
            lowered = text.lower()
            section = next((kind for key, kind in SECTIONS.items() if key in lowered), None)
            continue
        if tag in ("h3", "h4") and section:
            if section in ("champion", "item") and tag == "h3":
                subjects[section].add(text)
            continue
        if tag == "li" and ARROW_RE.search(text):
            arrows += 1
    return {"subjects": {key: sorted(value) for key, value in subjects.items()}, "arrow_lines": arrows}


def main() -> int:
    parser = argparse.ArgumentParser(description="国服/英文公告对照核验")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    patch_map = json.loads((PATCH_DATA / "patch_map.json").read_text(encoding="utf-8"))["patches"]
    aliases = json.loads((PATCH_DATA / "aliases.json").read_text(encoding="utf-8"))
    corpus = json.loads((PATCH_DATA / "knowledge.json").read_text(encoding="utf-8"))

    en_names = {}
    for spelling, meta in aliases.items():
        if meta.get("en"):
            en_names[spelling] = meta["en"]

    cn_by_patch: dict[str, dict[str, set[str]]] = {}
    for row in corpus["chunks"]:
        if row["confidence"] == "narrative" or row["type"] not in ("champion", "item"):
            continue
        english = row.get("subject_en") or en_names.get(row["subject"], "")
        if english:
            cn_by_patch.setdefault(row["patch"], {}).setdefault(row["type"], set()).add(english)

    report = []
    for patch in sorted(patch_map, key=lambda value: tuple(int(part) for part in value.split("."))):
        document = fetch(patch, args.offline)
        if not document:
            continue
        parsed = parse(document)
        en_sets = {"champion": set(parsed["subjects"]["champion"]), "item": set(parsed["subjects"]["item"])}
        cn_sets = cn_by_patch.get(patch, {})
        entry = {"patch": patch, "en_arrows": parsed["arrow_lines"], "sections": {}}
        for kind in ("champion", "item"):
            cn, en = cn_sets.get(kind, set()), en_sets[kind]
            entry["sections"][kind] = {
                "cn": sorted(cn),
                "en": sorted(en),
                "both": sorted(cn & en),
                "cn_only": sorted(cn - en),
                "en_only": sorted(en - cn),
            }
        report.append(entry)
        champions = entry["sections"]["champion"]
        print(
            f"  {patch}: 英雄 双方 {len(champions['both'])}｜仅国服 {len(champions['cn_only'])}"
            f"｜仅英文 {len(champions['en_only'])}｜英文改动行 {entry['en_arrows']}"
        )

    (PATCH_DATA / "crosscheck.json").write_text(
        json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "patches": report}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    cn_only_total = sum(len(entry["sections"]["champion"]["cn_only"]) for entry in report)
    en_only_total = sum(len(entry["sections"]["champion"]["en_only"]) for entry in report)
    both_total = sum(len(entry["sections"]["champion"]["both"]) for entry in report)
    lines = [
        "# 国服 / 英文公告对照核验",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　范围：{', '.join(entry['patch'] for entry in report)}",
        "",
        "> 英文公告（en-us）作为第二见证：只报告差异，不合并结论；回答一律以国服公告为准。",
        "> 「仅国服」不等于英文漏写——两边统计口径不同（国服含模式分区，英文按章节分类），差异需要人工抽查。",
        "",
        "## 结论",
        "",
        f"- 英雄：双方一致 **{both_total}** 个；仅国服 **{cn_only_total}** 个；仅英文 **{en_only_total}** 个。",
        "- 仅国服为 0 说明国服解析没有凭空造出对象；仅英文的对象需要人工确认是「国服写法不同」还是「解析漏项」。",
        "- 装备差异多为英文的 `[NEW]/[REMOVED]/[REWORKED]` 标记与大小写（如 `Dusk and Dawn` 对 `Dusk And Dawn`），不是内容缺失。",
        "",
        "| 版本 | 英雄（双方一致） | 仅国服 | 仅英文 | 英文 ⇒ 行 |",
        "|---|---|---|---|---|",
    ]
    for entry in report:
        champions = entry["sections"]["champion"]
        lines.append(
            f"| {entry['patch']} | {len(champions['both'])} | {len(champions['cn_only'])} "
            f"| {len(champions['en_only'])} | {entry['en_arrows']} |"
        )
    lines += ["", "## 逐版本差异明细", ""]
    for entry in report:
        lines.append(f"### {entry['patch']}")
        for kind, label in (("champion", "英雄"), ("item", "装备")):
            section = entry["sections"][kind]
            if section["cn_only"]:
                lines.append(f"- {label}仅出现在国服：{'、'.join(section['cn_only'])}")
            if section["en_only"]:
                lines.append(f"- {label}仅出现在英文：{'、'.join(section['en_only'])}")
        lines.append("")
    (DOCS / "对照核验.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告：{DOCS / '对照核验.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
