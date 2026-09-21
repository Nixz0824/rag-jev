"""Build the ability-name dictionary from Data Dragon.

Players ask by skill name far more often than by letter ("亚索的斩钢闪改了吗"), so the
parser needs a name -> Q/W/E/R map. Data Dragon's championFull carries the official
Chinese names, so this stays self-contained.

Optionally cross-checks the generated names against another LoL term dictionary: point
RAGJEV_TERMS at a JSON file whose "terms" list carries category/chinese fields. The
comparison is printed as validation; the file is never required for the build.

Usage:
    python scripts/build_abilities.py                 # fetch/cached Data Dragon
    python scripts/build_abilities.py --offline       # cached only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import PATCH_DATA, RAW  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36"
# Optional external dictionary for a cross-check, e.g. another project's Data Dragon export.
CROSS_CHECK_TERMS = os.environ.get("RAGJEV_TERMS", "")
SLOTS = ("Q", "W", "E", "R")


def fetch_json(url: str, cache: Path, offline: bool) -> dict:
    if cache.exists() and offline:
        return json.loads(cache.read_text(encoding="utf-8"))
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload
    except Exception as error:  # noqa: BLE001
        if cache.exists():
            print(f"  网络失败，使用缓存 {cache.name}：{error}")
            return json.loads(cache.read_text(encoding="utf-8"))
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="生成技能名字典")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)

    versions = fetch_json(
        "https://ddragon.leagueoflegends.com/api/versions.json", RAW / "ddragon-versions.json", args.offline
    )
    version = versions[0]
    full = fetch_json(
        f"https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN/championFull.json",
        RAW / "championFull-zh_CN.json",
        args.offline,
    )
    aliases = json.loads((PATCH_DATA / "aliases.json").read_text(encoding="utf-8"))
    learned_file = PATCH_DATA / "learned_names.json"
    learned = json.loads(learned_file.read_text(encoding="utf-8")) if learned_file.exists() else {}

    def subject_for(english: str, fallback: str) -> str:
        """The spelling this project actually uses: corpus-learned name, then the
        alias table's primary, then whatever Data Dragon calls it."""
        if learned.get(english):
            return learned[english]
        for name, meta in aliases.items():
            if meta.get("kind") == "champion" and meta.get("en") == english and meta.get("primary") == name:
                return name
        return fallback

    abilities: dict[str, dict] = {}
    for key, champion in (full.get("data") or {}).items():
        names: dict[str, str] = {}
        passive = (champion.get("passive") or {}).get("name", "")
        if passive:
            names["被动"] = passive
        for slot, spell in zip(SLOTS, champion.get("spells") or []):
            name = (spell or {}).get("name", "")
            if name:
                names[slot] = name
        # The corpus stores champions under the announcement spelling; map through the
        # English id so the dictionary is keyed the same way as everything else.
        english = champion.get("id") or key
        chinese = subject_for(english, champion.get("name", ""))
        if chinese and names:
            abilities[chinese] = names

    (PATCH_DATA / "abilities.json").write_text(json.dumps(abilities, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(names) for names in abilities.values())
    print(f"Data Dragon {version}：英雄 {len(abilities)} 个、技能名 {total} 条 → {PATCH_DATA / 'abilities.json'}")

    if CROSS_CHECK_TERMS and Path(CROSS_CHECK_TERMS).exists():
        terms = json.loads(Path(CROSS_CHECK_TERMS).read_text(encoding="utf-8")).get("terms", [])
        theirs = {term["chinese"] for term in terms if term.get("category") == "championAbility" and term.get("chinese")}
        ours = {name for names in abilities.values() for name in names.values()}
        shared = theirs & ours
        print(
            f"交叉核对（外部词典 {Path(CROSS_CHECK_TERMS).name}）：技能名 {len(theirs)} 条，与本字典重合 {len(shared)} 条"
            f"（{len(shared) / max(len(theirs), 1):.0%}）；本字典独有 {len(ours - theirs)} 条"
        )
        if theirs - ours:
            print("  对方有、本字典没有的样例：" + "、".join(sorted(theirs - ours)[:8]))
    else:
        print("未设置 RAGJEV_TERMS，跳过交叉核对（不影响构建）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
