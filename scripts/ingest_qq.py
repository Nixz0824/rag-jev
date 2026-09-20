"""Ingest official CN patch announcements (lol.qq.com) into structured chunks.

The announcement body is rich text, not an API: sections are ``h3``, subjects are
``h4``, and one change is a line like ``冷却时间：4 ⇒ 3.5`` where the arrow is the
HTML entity ``&rArr;``. Images in the body are 30px ability icons, not data.

Usage:
    python scripts/ingest_qq.py --count 10        # fetch/refresh the newest patches
    python scripts/ingest_qq.py --offline         # rebuild from cached HTML only
    python scripts/ingest_qq.py --patch 26.17     # one patch
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DOCS, PATCH_DATA, RAW  # noqa: E402
from patch_schema import (  # noqa: E402
    ABILITY_LINE_RE,
    ARROW_RE,
    BRACKET_RE,
    CHANGE_LINE_RE,
    MODE_ORDER,
    chunk_id,
    clean,
    detect_mode,
    direction,
    field_key,
    sentence,
    split_subject,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LIST_API = "https://apps.game.qq.com/cmc/zmMcnTargetContentList?r0=json&page={page}&num=30&target=23&type=2"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36"
TITLE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})版本(?:更新)?公告")
# Some patches are announced under a maintenance-date title instead of a number,
# e.g. "8月13日1点停机版本更新公告"; their version is resolved from the article.
DATE_TITLE_RE = re.compile(r"^\d{1,2}月\d{1,2}日.*(停机)?版本更新公告$")
VERSION_IN_ARTICLE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\s*版本")
WHITELIST_SECTIONS = ("版本要闻", "英雄", "装备", "经典模式", "海克斯大乱斗", "斗魂竞技场", "BUG修复及品质级改动")
SKIP_SECTIONS = ("即将到来的皮肤", "版本要闻", "BUG修复", "品质级改动", "云顶", "TA的文章", "相关推荐")
MODE_SECTIONS = {"经典模式": "classic", "海克斯大乱斗": "aram", "斗魂竞技场": "arena"}
MODE_LABELS = {"rift": "召唤师峡谷", "classic": "经典模式", "aram": "海克斯大乱斗", "arena": "斗魂竞技场", "other": "其他"}
SKILL_FIELDS = {"damage", "cooldown", "cost", "range", "shield", "heal"}
BASE_STATS_RE = re.compile(r"^基础(属性|数值)$")
BLOCK_RE = re.compile(r"<(h3|h4|p)\b[^>]*>(.*?)</\1>", re.S | re.I)
LIST_ITEM_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I)


def http_text(url: str, encoding: str = "utf-8", timeout: int = 45) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode(encoding, "replace")


def http_json(url: str, timeout: int = 45) -> dict:
    return json.loads(http_text(url, timeout=timeout))


def fetch_listing(pages: int) -> tuple[dict[str, dict], list[dict]]:
    """Collect patch announcements from the official 公告 channel.

    Returns numbered announcements plus date-titled candidates whose version
    number has to be read out of the article itself.
    """
    found: dict[str, dict] = {}
    candidates: list[dict] = []
    for page in range(1, pages + 1):
        try:
            payload = http_json(LIST_API.format(page=page))
        except Exception as error:  # noqa: BLE001 - listing is best effort
            print(f"  列表第 {page} 页失败：{error}")
            break
        items = (payload.get("data") or {}).get("result") or []
        if not items:
            break
        for item in items:
            title = (item.get("sTitle") or "").strip()
            url = item.get("sRedirectURL") or ""
            if not url:
                continue
            meta = {"url": url, "date": (item.get("sIdxTime") or "")[:10], "title": title}
            match = TITLE_RE.match(title)
            if match:
                patch = f"{int(match.group(1))}.{int(match.group(2))}"
                found.setdefault(patch, {**meta, "patch": patch})
            elif DATE_TITLE_RE.match(title):
                candidates.append(meta)
        time.sleep(0.4)
    return found, candidates


def resolve_patch(document: str) -> str | None:
    """Read the patch number out of a maintenance-date-titled announcement."""
    for match in re.finditer(r"<h1[^>]*>(.*?)</h1>", document, re.S | re.I):
        version = VERSION_IN_ARTICLE_RE.search(clean(re.sub(r"<[^>]+>", "", match.group(1))))
        if version:
            return f"{int(version.group(1))}.{int(version.group(2))}"
    released = re.search(r"发布\s*(\d{1,2})\.(\d{1,2})\s*版本", document)
    if released:
        return f"{int(released.group(1))}.{int(released.group(2))}"
    return None


def article_body(document: str) -> str:
    """Cut the share/related footer off the article so it cannot leak into parsing."""
    start = document.find('id="article"')
    body = document[start:] if start >= 0 else document
    for marker in ("TA的文章", "相关推荐", "即将到来的皮肤"):
        index = body.find(marker)
        if index > 0:
            body = body[:index]
    return body


BOLD = "\ue000"


def lines_of(fragment: str) -> list[str]:
    """Split a paragraph into lines, marking <strong>/<b> runs as bold subjects."""
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<img[^>]*>", "", text, flags=re.I)
    text = re.sub(r"<(strong|b)\b[^>]*>(.*?)</\1>", lambda m: BOLD + m.group(2) + BOLD, text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return [clean(line) for line in html_lib.unescape(text).split("\n")]


def is_bold_subject(line: str) -> bool:
    inner = line.strip(BOLD).strip()
    if not (line.startswith(BOLD) and line.endswith(BOLD)):
        return False
    return 1 <= len(inner) <= 16 and not re.search(r"[\d：:]", inner)


def parse_article(patch: str, document: str, aliases: dict[str, dict]) -> tuple[list[dict], dict]:
    body = article_body(document)
    chunks: list[dict] = []
    stats = {
        "lines": 0,
        "arrow_lines": 0,
        "changes": 0,
        "narrative": 0,
        "unknown_field": 0,
        "no_ability": 0,
        "skipped": 0,
        "sections": {},
    }
    section = ""
    mode = "rift"
    subject = ""
    subject_title = ""
    subject_type = "system"
    ability = ""
    ability_name = ""
    skip_section = False
    unknown_labels: dict[str, int] = {}

    for tag, fragment in BLOCK_RE.findall(body):
        tag = tag.lower()
        if tag in ("h3", "h4"):
            heading = clean(re.sub(r"<[^>]+>", "", fragment))
            if not heading:
                continue
            if tag == "h3":
                section = heading
                skip_section = any(key in heading for key in SKIP_SECTIONS)
                mode = MODE_SECTIONS.get(heading, "rift" if heading not in MODE_SECTIONS else "other")
                if heading in MODE_SECTIONS:
                    mode = MODE_SECTIONS[heading]
                subject, subject_title, subject_type, ability, ability_name = "", "", "system", "", ""
                stats["sections"][heading] = stats["sections"].get(heading, 0)
            else:
                if skip_section:
                    continue
                if section in ("英雄", "装备"):
                    subject_title, name = split_subject(heading)
                    subject = name
                    subject_type = "champion" if section == "英雄" else "item"
                else:
                    subject = heading
                    subject_type = "mode" if mode != "rift" else "system"
                    subject_title = ""
                ability, ability_name = "", ""
            continue

        if skip_section:
            continue
        paragraph_ability, paragraph_ability_name = "", ""
        for line in lines_of(fragment):
            if not line:
                continue
            stats["lines"] += 1
            stats["sections"][section] = stats["sections"].get(section, 0) + 1
            if BASE_STATS_RE.match(line):
                ability, ability_name = "", ""
                continue
            if is_bold_subject(line):
                subject = line.replace(BOLD, "").strip()
                subject_title = ""
                subject_type = "mode" if mode != "rift" else "system"
                ability, ability_name = "", ""
                continue
            line = line.replace(BOLD, "").strip()
            bracket = BRACKET_RE.match(line)
            if bracket and len(line) <= 24:
                subject, subject_type = bracket.group("name"), "item" if section == "装备" else "system"
                subject_title = ""
                continue
            ability_match = ABILITY_LINE_RE.match(line)
            if ability_match and len(line) <= 32:
                paragraph_ability = ability_match.group("ability").upper().replace("被动", "被动")
                paragraph_ability_name = clean(ability_match.group("name"))
                ability, ability_name = paragraph_ability, paragraph_ability_name
                continue
            change = CHANGE_LINE_RE.match(line)
            if change and ARROW_RE.search(line):
                stats["arrow_lines"] += 1
                label = change.group("label").strip()
                key = field_key(label)
                if not key:
                    stats["unknown_field"] += 1
                    unknown_labels[label] = unknown_labels.get(label, 0) + 1
                resolved_ability = paragraph_ability or ability
                if key in SKILL_FIELDS and not resolved_ability:
                    resolved_ability = ""
                elif key not in SKILL_FIELDS:
                    resolved_ability = paragraph_ability or ""
                if key in SKILL_FIELDS and not resolved_ability:
                    stats["no_ability"] += 1
                row = {
                    "patch": patch,
                    "mode": mode,
                    "mode_label": MODE_LABELS.get(mode, mode),
                    "section": section or "未分区",
                    "type": subject_type,
                    "subject": subject or section or "未命名",
                    "subject_title": subject_title,
                    "ability": resolved_ability,
                    "ability_name": paragraph_ability_name or (ability_name if resolved_ability == ability else ""),
                    "field": label,
                    "field_key": key or "unknown",
                    "old_value": change.group("old").strip(),
                    "new_value": change.group("new").strip(),
                    "direction": direction(change.group("old"), change.group("new")),
                    "confidence": "high" if key and (resolved_ability or subject_type != "champion") else "limited",
                }
                row["id"] = chunk_id(patch, mode, row["subject"], row["ability"], row["field"], row["old_value"], row["new_value"])
                row["text"] = sentence(row)
                chunks.append(row)
                stats["changes"] += 1
                continue
            if len(line) >= 10 and re.search(r"[\u4e00-\u9fff]", line):
                row = {
                    "patch": patch,
                    "mode": mode,
                    "mode_label": MODE_LABELS.get(mode, mode),
                    "section": section or "未分区",
                    "type": subject_type,
                    "subject": subject or section or "未命名",
                    "subject_title": subject_title,
                    "ability": "",
                    "ability_name": "",
                    "field": "说明",
                    "field_key": "narrative",
                    "old_value": "",
                    "new_value": line[:400],
                    "direction": "adjust",
                    "confidence": "narrative",
                }
                row["id"] = chunk_id(patch, mode, row["subject"], "", "说明", "", line[:120])
                row["text"] = f"{patch} 版本（{row['section']}）：{row['subject']}——{line[:400]}"
                chunks.append(row)
                stats["narrative"] += 1
            else:
                stats["skipped"] += 1

    stats["unknown_labels"] = dict(sorted(unknown_labels.items(), key=lambda item: -item[1])[:12])
    for row in chunks:
        for key in ("subject", "subject_title", "ability", "ability_name", "field", "old_value", "new_value", "text"):
            if isinstance(row.get(key), str):
                row[key] = row[key].replace(BOLD, "").strip()
        meta = aliases.get(row["subject"]) or {}
        row.update(
            {
                "subject_en": meta.get("en", ""),
                "aliases": meta.get("aliases", []),
                "source_title": f"国服 {patch} 版本更新公告",
                "source_url": "",
                "source_type": "qq_patch_notes",
                "keywords": " ".join(
                    filter(
                        None,
                        [
                            row["subject"],
                            row["subject_title"],
                            meta.get("en", ""),
                            *meta.get("aliases", [])[:4],
                            row["patch"],
                            row["field"],
                            row["ability"],
                            {"buff": "加强", "nerf": "削弱"}.get(row["direction"], ""),
                        ],
                    )
                ),
            }
        )
    return chunks, stats


def ddragon_map(patch: str, versions: list[str]) -> str:
    major, minor = patch.split(".")
    target = f"{int(major) - 10}.{minor}."
    for version in versions:
        if version.startswith(target):
            return version
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取国服版本更新公告并生成结构化语料")
    parser.add_argument("--count", type=int, default=10, help="收录最近多少个版本")
    parser.add_argument("--pages", type=int, default=30, help="列表接口翻页数")
    parser.add_argument("--patch", action="append", default=[], help="只处理指定版本号，如 26.17")
    parser.add_argument("--offline", action="store_true", help="只用已缓存的 HTML，不联网")
    args = parser.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    aliases_file = PATCH_DATA / "aliases.json"
    aliases = json.loads(aliases_file.read_text(encoding="utf-8")) if aliases_file.exists() else {}

    versions_file = RAW / "ddragon-versions.json"
    versions: list[str] = json.loads(versions_file.read_text(encoding="utf-8")) if versions_file.exists() else []
    if not versions and not args.offline:
        try:
            versions = http_json("https://ddragon.leagueoflegends.com/api/versions.json")
            versions_file.write_text(json.dumps(versions, ensure_ascii=False), encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            print(f"  Data Dragon 版本列表获取失败：{error}")

    if args.patch:
        wanted = {patch_key: {} for patch_key in args.patch}
    else:
        wanted: dict[str, dict] = {}
        if not args.offline:
            numbered, candidates = fetch_listing(args.pages)
            wanted.update(numbered)
            # Date-titled announcements ("8月13日1点停机版本更新公告") are resolved
            # from the article itself; there are only a handful even in long scans.
            for candidate in candidates[:12]:
                try:
                    document = http_text(candidate["url"], encoding="gbk")
                    time.sleep(0.4)
                except Exception as error:  # noqa: BLE001
                    print(f"  候选公告抓取失败：{error}")
                    continue
                patch = resolve_patch(document)
                if not patch or patch in wanted:
                    continue
                (RAW / f"qq-{patch}.html").write_text(document, encoding="utf-8")
                wanted[patch] = {**candidate, "patch": patch}
                print(f"  从日期标题公告解析出 {patch}（{candidate['date']}）")
        if not wanted:
            for cached in RAW.glob("qq-*.html"):
                wanted[cached.stem.replace("qq-", "", 1)] = {}
        wanted = dict(
            sorted(wanted.items(), key=lambda item: tuple(int(x) for x in item[0].split(".")), reverse=True)[: args.count]
        )

    all_chunks: list[dict] = []
    patch_map: dict[str, dict] = {}
    report: list[dict] = []
    for patch, meta in sorted(wanted.items(), key=lambda item: tuple(int(x) for x in item[0].split("."))):
        cache = RAW / f"qq-{patch}.html"
        url = (meta or {}).get("url", "")
        if not cache.exists() and url and not args.offline:
            try:
                cache.write_text(http_text(url, encoding="gbk"), encoding="utf-8")
                time.sleep(0.6)
            except Exception as error:  # noqa: BLE001
                print(f"  {patch} 抓取失败：{error}")
                continue
        if not cache.exists():
            print(f"  {patch} 没有缓存，跳过")
            continue
        chunks, stats = parse_article(patch, cache.read_text(encoding="utf-8"), aliases)
        for row in chunks:
            row["source_url"] = url or row["source_url"]
        all_chunks += chunks
        patch_map[patch] = {
            "date": (meta or {}).get("date", ""),
            "url": url,
            "ddragon": ddragon_map(patch, versions),
            "chunks": len(chunks),
            "chars": len(cache.read_text(encoding="utf-8")),
            "modes": sorted({row["mode"] for row in chunks}, key=lambda m: MODE_ORDER.get(m, 9)),
        }
        report.append({"patch": patch, **stats})
        print(
            f"  {patch}: 变化 {stats['changes']} 条、叙述 {stats['narrative']} 条、"
            f"未识别字段 {stats['unknown_field']}、无技能归属 {stats['no_ability']}"
        )

    if not all_chunks:
        print("没有生成任何语料。")
        return 1

    corpus = {
        "title": "RAG Jev：国服版本更新公告结构化语料",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_note": "数据来源为腾讯国服《英雄联盟》版本更新公告（lol.qq.com）；本文件仅本地使用，不随仓库发布。",
        "stats": {
            "patches": len(patch_map),
            "chunks": len(all_chunks),
            "structured": sum(1 for row in all_chunks if row["confidence"] in ("high", "limited")),
            "narrative": sum(1 for row in all_chunks if row["confidence"] == "narrative"),
            "unknown_field": sum(1 for row in all_chunks if row["field_key"] == "unknown"),
        },
        "chunks": all_chunks,
    }
    (PATCH_DATA / "knowledge.json").write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
    learned = {}
    for row in all_chunks:
        if row["type"] in ("champion", "item") and row.get("subject_en") and row["subject"] != row.get("subject_title"):
            learned.setdefault(row["subject_en"], row["subject"])
    (PATCH_DATA / "learned_names.json").write_text(
        json.dumps(dict(sorted(learned.items())), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (PATCH_DATA / "patch_map.json").write_text(
        json.dumps(
            {
                "generated_at": corpus["generated_at"],
                "note": "patch 为公告版本号；ddragon 为对应 Data Dragon 版本；github 仓库不包含公告原文。",
                "patches": patch_map,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = ["# 解析覆盖率", "", f"生成时间：{corpus['generated_at']}", "", 
             "| 版本 | 文本行 | 含⇒行 | 解析变化 | 叙述行 | 未识别字段 | 无技能归属 | 跳过 |",
             "|---|---|---|---|---|---|---|---|"]
    for item in report:
        lines.append(
            f"| {item['patch']} | {item['lines']} | {item['arrow_lines']} | {item['changes']} | {item['narrative']} |"
            f" {item['unknown_field']} | {item['no_ability']} | {item['skipped']} |"
        )
    total_changes = sum(item["changes"] for item in report)
    total_arrows = sum(item["arrow_lines"] for item in report)
    lines += [
        "",
        f"合计：结构化变化 {total_changes} 条 / 含箭头行 {total_arrows} 行"
        + (f"（转化率 {total_changes / total_arrows:.1%}）" if total_arrows else ""),
        "",
        "说明：转化率低于 100% 的行主要是同一行里包含多个数值、或字段名无法归一到固定键；",
        "叙述行不参与数值回答，只用于检索上下文。",
    ]
    unknown: dict[str, int] = {}
    for item in report:
        for label, count in (item.get("unknown_labels") or {}).items():
            unknown[label] = unknown.get(label, 0) + count
    if unknown:
        lines += ["", "## 未识别字段（出现次数）", ""]
        lines += [f"- {label}：{count}" for label, count in sorted(unknown.items(), key=lambda kv: -kv[1])]
    (DOCS / "解析覆盖率.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n语料：{len(all_chunks)} 条 → {PATCH_DATA / 'knowledge.json'}")
    print(f"覆盖率报告：{DOCS / '解析覆盖率.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
