"""Build data/patch/aliases.json from Data Dragon plus a hand-written slang list.

Data Dragon has shipped the Chinese ``name`` and ``title`` fields in either order
over the years, so every champion is indexed under both, with the English id as
the stable key. Slang is keyed by the English id for the same reason.

Usage:
    python scripts/build_aliases.py            # uses the latest Data Dragon version
    python scripts/build_aliases.py --offline  # cached data only
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import PATCH_DATA, RAW  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36"

# English champion id -> player nicknames, typos and shorthand.
SLANG: dict[str, list[str]] = {
    "Yasuo": ["压缩"],
    "Zed": [],
    "Ezreal": ["ez", "EZ", "小黄毛"],
    "Orianna": ["发条", "发条魔灵"],
    "Anivia": ["冰鸟"],
    "Ahri": ["狐狸"],
    "MasterYi": ["剑圣", "易大师"],
    "Garen": ["草丛伦"],
    "Vayne": ["VN", "vn"],
    "Lucian": ["奥巴马", "圣枪"],
    "Soraka": ["奶妈"],
    "Sona": ["琴女"],
    "TahmKench": ["蛤蟆"],
    "Aatrox": ["剑魔"],
    "Evelynn": ["寡妇"],
    "Akali": ["阿卡利", "akl"],
    "Diana": ["皎月"],
    "Leona": ["日女"],
    "Shyvana": ["龙女"],
    "Nidalee": ["豹女"],
    "Volibear": ["狗熊", "熊"],
    "Tryndamere": ["蛮王"],
    "XinZhao": ["菊花信"],
    "JarvanIV": ["皇子"],
    "Azir": ["沙皇"],
    "Swain": ["乌鸦"],
    "Vladimir": ["吸血鬼"],
    "Karthus": ["死歌"],
    "KogMaw": ["大嘴"],
    "Chogath": ["大虫子"],
    "Brand": ["火男"],
    "Smolder": ["小火龙"],
    "Senna": ["塞纳"],
    "Seraphine": ["酸辣粉"],
    "Varus": ["维鲁斯"],
    "Khazix": ["螳螂"],
    "Rengar": ["狮子狗"],
    "Urgot": ["螃蟹"],
    "Pyke": ["水鬼"],
    "Sett": ["腕豪"],
    "Viego": ["破败王"],
    "Gwen": ["剪刀妹"],
    "Belveth": ["大卑"],
    "Ornn": ["山羊"],
    "Warwick": ["狼人"],
    "Tristana": ["小炮"],
    "Fiora": ["剑姬"],
    "Irelia": ["刀妹"],
    "Talon": ["男刀"],
    "Leblanc": ["妖姬"],
    "Syndra": ["球女"],
    "Nasus": ["狗头"],
    "Renekton": ["鳄鱼"],
    "Vex": ["熬夜波比"],
    "Yorick": ["掘墓"],
    "Zilean": ["时光老头"],
    "Nami": ["美人鱼"],
    "Sivir": ["轮子妈"],
    "Twitch": ["老鼠"],
    "Mundo": ["蒙多医生"],
    "Trundle": ["巨魔"],
    "Kayle": ["天使"],
    "Morgana": ["堕落天使"],
    "Kaisa": ["凯莎"],
    "Veigar": ["小法师"],
    "Annie": ["火女"],
    "Nunu": ["雪人"],
    "Malphite": ["石头人"],
    "Amumu": ["木乃伊"],
    "Rammus": ["龟龟"],
    "Zac": ["史莱姆"],
    "Velkoz": ["大眼"],
    "RekSai": ["挖掘机"],
    "Kalista": ["滑板鞋"],
    "Jinx": ["爆爆"],
    "Alistar": ["牛头", "牛头人"],
    "Rammus": ["龙龟"],
    "Caitlyn": ["女警"],
    "Viktor": ["三只手", "机械先驱"],
    "Corki": ["飞机"],
    "Nautilus": ["泰坦"],
    "Sejuani": ["猪妹"],
    "TwistedFate": ["卡牌", "卡牌大师"],
    "Fizz": ["小鱼人"],
    "Graves": ["男枪"],
    "MissFortune": ["女枪"],
    "Maokai": ["大树"],
    "Elise": ["蜘蛛"],
    "Skarner": ["蝎子"],
    "Taric": ["宝石"],
    "Neeko": ["万花通灵"],
    "Malzahar": ["虚空先知"],
    "Gangplank": ["船长"],
    "Azir": ["鸟皇"],
    "Wukong": ["猴子"],
    "Darius": ["诺手"],
    "Mordekaiser": ["铁男"],
    "Lissandra": ["冰女"],
    "Fiddlesticks": ["稻草人"],
    "Singed": ["炼金"],
    "Ziggs": ["炸弹人"],
    "Cassiopeia": ["蛇女"],
    "Karma": ["扇子妈"],
    "Blitzcrank": ["机器人"],
    "Pantheon": ["潘森"],
    "Rumble": ["兰博"],
}


def fetch_json(url: str, cache: Path, offline: bool) -> dict:
    if cache.exists() and offline:
        return json.loads(cache.read_text(encoding="utf-8"))
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload
    except Exception as error:  # noqa: BLE001
        if cache.exists():
            print(f"  网络失败，使用缓存 {cache.name}：{error}")
            return json.loads(cache.read_text(encoding="utf-8"))
        raise


def latest_version(offline: bool) -> str:
    payload = fetch_json("https://ddragon.leagueoflegends.com/api/versions.json", RAW / "ddragon-versions.json", offline)
    return payload[0]


def learned_names() -> dict[str, str]:
    """Names the ingest actually saw in announcements, keyed by English id."""
    path = PATCH_DATA / "learned_names.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成英雄/装备别名表")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)

    version = latest_version(args.offline)
    print("Data Dragon 版本：", version)
    base = f"https://ddragon.leagueoflegends.com/cdn/{version}/data"
    zh_champions = fetch_json(f"{base}/zh_CN/champion.json", RAW / "champion-zh_CN.json", args.offline)["data"]
    en_champions = fetch_json(f"{base}/en_US/champion.json", RAW / "champion-en_US.json", args.offline)["data"]
    tw_champions = fetch_json(f"{base}/zh_TW/champion.json", RAW / "champion-zh_TW.json", args.offline)["data"]
    zh_items = fetch_json(f"{base}/zh_CN/item.json", RAW / "item-zh_CN.json", args.offline)["data"]
    en_items = fetch_json(f"{base}/en_US/item.json", RAW / "item-en_US.json", args.offline)["data"]
    tw_items = fetch_json(f"{base}/zh_TW/item.json", RAW / "item-zh_TW.json", args.offline)["data"]

    aliases: dict[str, dict] = {}
    learned = learned_names()
    for key, champion in zh_champions.items():
        english = en_champions.get(key, {})
        english_name = english.get("name") or key
        chinese_name, chinese_title = champion.get("name", ""), champion.get("title", "")
        if not chinese_name:
            continue
        # Data Dragon has shipped name and title in either order; the announcement
        # style is "称号 名字", and the ingest records what it actually saw.
        primary = learned.get(english_name) or chinese_title or chinese_name
        slang = SLANG.get(key, [])
        # Taiwan spellings are accepted too (犽宿 → 亚索), they just never become primary.
        taiwan = tw_champions.get(key, {})
        for spelling in {chinese_name, chinese_title, taiwan.get("name", ""), taiwan.get("title", "")}:
            if not spelling:
                continue
            entries = {chinese_name, chinese_title, key, english_name, *slang}
            aliases[spelling] = {
                "en": english_name,
                "kind": "champion",
                "primary": primary,
                "aliases": sorted(entry for entry in entries if entry and entry != spelling),
            }
    items = 0
    for item_id, item in zh_items.items():
        chinese_name = item.get("name", "")
        english_name = en_items.get(item_id, {}).get("name", "")
        taiwan_name = tw_items.get(item_id, {}).get("name", "")
        if not chinese_name or not english_name or item.get("maps", {}).get("11") is not True:
            continue
        entries = {english_name, item_id, taiwan_name, *SLANG.get(chinese_name, [])}
        aliases[chinese_name] = {
            "en": english_name,
            "kind": "item",
            "aliases": sorted(entry for entry in entries if entry and entry != chinese_name),
        }
        items += 1

    PATCH_DATA.mkdir(parents=True, exist_ok=True)
    (PATCH_DATA / "aliases.json").write_text(json.dumps(aliases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"别名键 {len(aliases)} 个（英雄 {len(zh_champions)}、装备 {items}）→ {PATCH_DATA / 'aliases.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
