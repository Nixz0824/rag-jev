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
    "Riven": ["瑞文", "瑞雯"],
    "Vayne": ["VN", "vn", "维恩"],
    "Lucian": ["奥巴马", "圣枪", "卢西安"],
    "Aphelios": ["厄斐流斯"],
    "Talon": ["男刀", "泰龙"],
    "Camille": ["卡密尔", "青钢影"],
    "Zoe": ["佐依"],
    "Zyra": ["捷拉"],
    "Viego": ["破败王", "佛耶哥"],
    "Gwen": ["剪刀妹", "格文"],
    "Kalista": ["滑板鞋", "卡莉斯塔"],
    "Leona": ["日女", "雷欧娜"],
    "Seraphine": ["酸辣粉", "萨勒芬尼"],
    "Milio": ["米里欧"],
    "Braum": ["布隆", "布朗"],
    "Shaco": ["小丑"],
    "Katarina": ["卡特"],
    "Ryze": ["流浪"],
    "Lux": ["光辉"],
    "Sion": ["亡灵"],
    "Malphite": ["石头人"],
    "Amumu": ["木乃伊"],
    "Zac": ["史莱姆"],
    "Kennen": ["电耗子"],
    "Nunu": ["雪人"],
    "Nidalee": ["豹女"],
    "Twitch": ["老鼠"],
    "Rengar": ["狮子狗"],
    "Khazix": ["螳螂"],
    "RekSai": ["挖掘机"],
    "Velkoz": ["大眼"],
    "KogMaw": ["大嘴"],
    "TahmKench": ["蛤蟆"],
    "Braum": ["布隆"],
    "Illaoi": ["触手妈"],
    "AurelionSol": ["龙王"],
    "Yuumi": ["猫"],
    "Zeri": ["泽丽"],
    "Belveth": ["大卑"],
    "Naafiri": ["狗"],
    "Ambessa": ["安蓓萨"],
    "Yunara": ["芸阿娜"],
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


# Item nicknames, matched against the official Chinese name by keyword. Rules that
# match no item in the current Data Dragon are reported, so stale entries are visible.
ITEM_SLANG_RULES: tuple[tuple[str, list[str]], ...] = (
    ("斯塔缇克", ["电刀", "电刃"]),
    ("无尽之刃", ["无尽"]),
    ("死亡之帽", ["帽子", "大帽", "死帽"]),
    ("卢登", ["卢登"]),
    ("冰晶节杖", ["冰杖"]),
    ("荆棘之甲", ["反甲"]),
    ("饮血剑", ["饮血"]),
    ("三相之力", ["三项"]),
    ("鬼索", ["羊刀"]),
    ("黑色切割者", ["黑切"]),
    ("破败王者", ["破败"]),
    ("守护天使", ["复活甲", "天使甲"]),
    ("中娅沙漏", ["沙漏", "中娅"]),
    ("莫雷洛", ["鬼书"]),
    ("兰德里的折磨", ["大面具"]),
    ("巫妖之祸", ["巫妖"]),
    ("斯特拉克", ["血手"]),
    ("死亡之舞", ["死舞"]),
    ("水银", ["水银"]),
    ("铁板靴", ["布甲鞋"]),
    ("狂战士胫甲", ["攻速鞋"]),
    ("法师之靴", ["法穿鞋"]),
    ("疾行之靴", ["五速鞋"]),
    ("明朗之靴", ["cd鞋"]),
    ("帝国指令", ["帝国"]),
    ("炽热香炉", ["香炉"]),
    ("骑士之誓", ["骑士"]),
    ("正义荣耀", ["正义荣耀"]),
    ("基克的", ["基克"]),
    ("幽梦之灵", ["幽梦"]),
    ("暮刃", ["暮刃"]),
    ("公理圆弧", ["公理"]),
    ("夜之锋刃", ["夜刃"]),
    ("海克斯科技火箭腰带", ["火箭腰带"]),
    ("原生质护带", ["护带"]),
    ("振奋盔甲", ["振奋"]),
    ("亡者的板甲", ["板甲"]),
    ("兰顿之兆", ["兰顿"]),
    ("冰霜之心", ["冰心"]),
    ("深渊面具", ["深渊"]),
    ("智慧末刃", ["末刃"]),
    ("纳什之牙", ["纳什"]),
    ("海妖杀手", ["海妖"]),
    ("多米尼克领主的致意", ["大轻语"]),
    ("最后的轻语", ["轻语"]),
    ("疾射火炮", ["火炮"]),
    ("幻影之舞", ["绿叉"]),
    ("卢安娜的飓风", ["飓风"]),
    ("凡性的提醒", ["重伤弓"]),
    ("大天使之杖", ["大天使", "泪杖"]),
    ("时光之杖", ["时光杖"]),
    ("炽天使之拥", ["炽天使"]),
    ("海克斯科技枪刃", ["科技枪"]),
    ("朔极之矛", ["青龙刀"]),
    ("神圣分离者", ["分离者"]),
    ("暗夜收割者", ["收割者"]),
    ("玛莫提乌斯", ["饮魔刀"]),
    ("贪欲九头蛇", ["九头蛇"]),
    ("巨型九头蛇", ["九头蛇"]),
    ("钢铁烈阳之匣", ["鸟盾"]),
    ("舒瑞娅", ["战歌"]),
    ("救赎", ["救赎"]),
    ("多兰", ["多兰"]),
    ("暴风之剑", ["大剑"]),
    ("水银之靴", ["水银鞋"]),
    ("铁板靴", ["布甲鞋", "忍者鞋"]),
    ("幽魂面具", ["小面具"]),
    ("巨型九头蛇", ["九头蛇", "巨九"]),
    ("贪欲九头蛇", ["九头蛇", "贪九"]),
    ("卢安娜的飓风", ["飓风", "分裂弓"]),
    ("幻影之舞", ["绿叉", "幻舞"]),
    ("石像鬼石板甲", ["石像鬼"]),
    ("心之钢", ["心钢"]),
    ("巨蛇之牙", ["巨蛇"]),
    ("深渊面具", ["深渊"]),
    ("适应性头盔", ["头盔"]),
    ("无尽之刃", ["无尽", "IE", "ie"]),
    ("饮血剑", ["饮血", "BT", "bt"]),
    ("守护天使", ["复活甲", "GA", "ga"]),
    ("水银饰带", ["水银", "QSS", "qss"]),
    ("幻影之舞", ["绿叉", "PD", "pd"]),
    ("最后的轻语", ["轻语", "LW", "lw"]),
    ("死亡之帽", ["帽子", "大帽", "DC", "dc"]),
    ("时光之杖", ["时光杖", "ROA", "roa"]),
    ("三相之力", ["三项", "TF", "tf"]),
    ("破败王者", ["破败", "BotRK", "botrk"]),
    ("中娅沙漏", ["沙漏", "中娅", "Zhonya", "zhonya"]),
    ("卢登", ["卢登", "Luden", "luden"]),
    ("莫雷洛", ["鬼书", "Morello", "morello"]),
    ("兰德里的折磨", ["大面具", "Liandry", "liandry"]),
    ("虚空之杖", ["虚空杖", "Void Staff"]),
    ("灭世者的死亡之帽", ["死帽"]),
    ("卢安娜的飓风", ["飓风", "Runaan", "runaan"]),
    ("智慧末刃", ["末刃", "Wit's End"]),
    ("纳什之牙", ["纳什", "Nashor", "nashor"]),
    ("海妖杀手", ["海妖", "Kraken", "kraken"]),
    ("收集者", ["收集者", "Collector", "collector"]),
    ("斯特拉克", ["血手", "Sterak", "sterak"]),
    ("朔极之矛", ["青龙刀", "Shurelya"]),
    ("舒瑞娅", ["战歌", "Shurelya", "shurelya"]),
    ("米凯尔的祝福", ["坩埚", "Mikael", "mikael"]),
    ("兰顿之兆", ["兰顿", "Randuin", "randuin"]),
    ("冰霜之心", ["冰心", "Frozen Heart"]),
    ("日炎圣盾", ["日炎", "Sunfire", "sunfire"]),
    ("荆棘之甲", ["反甲", "Thornmail", "thornmail"]),
    ("振奋盔甲", ["振奋", "Spirit Visage"]),
    ("狂徒铠甲", ["狂徒", "Warmog", "warmog"]),
    ("幽梦之灵", ["幽梦", "Ghostblade", "ghostblade"]),
    ("死亡之舞", ["死舞", "Death's Dance"]),
    ("多米尼克领主的致意", ["大轻语", "Dominik", "dominik"]),
    ("凡性的提醒", ["重伤弓", "Mortal Reminder"]),
    ("玛莫提乌斯", ["饮魔刀", "Maw"]),
)


def item_slang(chinese_name: str) -> list[str]:
    """Nicknames for one item, matched by keyword."""
    found: list[str] = []
    for keyword, names in ITEM_SLANG_RULES:
        if keyword in chinese_name:
            found += names
    return found


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
    used_rules: set[str] = set()
    for item_id, item in zh_items.items():
        chinese_name = item.get("name", "")
        english_name = en_items.get(item_id, {}).get("name", "")
        taiwan_name = tw_items.get(item_id, {}).get("name", "")
        if not chinese_name or not english_name or item.get("maps", {}).get("11") is not True:
            continue
        slang = item_slang(chinese_name)
        used_rules |= {keyword for keyword, _ in ITEM_SLANG_RULES if keyword in chinese_name}
        entries = {english_name, item_id, taiwan_name, *slang}
        aliases[chinese_name] = {
            "en": english_name,
            "kind": "item",
            "aliases": sorted(entry for entry in entries if entry and entry != chinese_name),
        }
        items += 1

    # Anything the corpus mentions but Data Dragon no longer calls that way (renamed items
    # like 卢登的配枪 → 卢登的回声, or internal codenames like C44) still has to resolve.
    for filename, label in (("learned_subjects.json", "语料导出"), ("knowledge.json", "语料正文")):
        path = PATCH_DATA / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = (
            [{"subject": subject, "type": kind, "subject_en": "", "aliases": []}
             for kind, subjects in payload.items() if isinstance(subjects, list) for subject in subjects]
            if filename == "learned_subjects.json"
            else payload.get("chunks", [])
        )
        added = []
        for row in rows:
            subject, kind = row.get("subject", ""), row.get("type", "")
            if kind not in ("champion", "item") or not subject or subject in aliases:
                continue
            english = row.get("subject_en") or ""
            entries = {english, *(row.get("aliases") or [])}
            aliases[subject] = {
                "en": english,
                "kind": kind,
                "aliases": sorted(entry for entry in entries if entry and entry != subject),
            }
            added.append(f"{subject}（{kind}）")
        if added:
            print(f"从{label}补入 {len(added)} 个对象（Data Dragon 未收录或已改名）：{'、'.join(added[:6])}")

    PATCH_DATA.mkdir(parents=True, exist_ok=True)
    (PATCH_DATA / "aliases.json").write_text(json.dumps(aliases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"别名键 {len(aliases)} 个（英雄 {len(zh_champions)}、装备 {items}）→ {PATCH_DATA / 'aliases.json'}")
    unmatched = [keyword for keyword, _ in ITEM_SLANG_RULES if keyword not in used_rules]
    if unmatched:
        print("以下装备俗称规则在当前 Data Dragon 里没有匹配到装备（可能是改名或已删除）：")
        for keyword in unmatched:
            print("   -", keyword)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
