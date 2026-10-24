#!/usr/bin/env python3
"""TVウォッチ: 各サイトから「日付と作品名」を集めて data.json を作る。
標準ライブラリだけで動く。GitHub Actions から毎日1回実行される想定。
取得元ごとに失敗しても他は続行し、失敗したタブは前回のデータを残す。
"""
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

JST = timezone(timedelta(hours=9))
OUT = Path(__file__).with_name("data.json")
UA = {"User-Agent": "Mozilla/5.0 (tv-watch personal use; once a day)"}
WEEK = "月火水木金土日"
TODAY = datetime.now(JST).date()


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8", errors="replace")


def to_lines(raw):
    """HTMLを改行区切りのテキストにする。"""
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    raw = re.sub(r"(?i)<(br|/p|/div|/li|/h\d|/tr|/dt|/dd)[^>]*>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", "", raw))
    return [re.sub(r"[ \t\u3000]+", " ", l).strip() for l in text.split("\n") if l.strip()]


def day_label(d):
    return f"{d.month}月{d.day}日({WEEK[d.weekday()]})"


def group_by_day(rows):
    """rows: [(date, item_dict)] -> days（日付順）"""
    days = {}
    for d, item in rows:
        days.setdefault(d, []).append(item)
    return [{"date": d.isoformat(), "label": day_label(d), "items": days[d]} for d in sorted(days)]


# ---------------------------------------------------------------- Amazon 配信終了
VARIANT = re.compile(r"[（(]\s*(?:日本語)?(?:字幕版?|吹替版?|字幕\s*[/／]\s*吹替|Dubbed)\s*[）)]|^[（(]字幕版[）)]|^[（(]吹替版[）)]")
PRIME_RE = re.compile(r"^(?P<t>.+?)\s*\[[^\]]*\]\s*(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日終了")


def parse_prime(raw, today=TODAY, horizon_days=75):
    seen, rows = set(), []
    for line in to_lines(raw):
        m = PRIME_RE.match(line)
        if not m:
            continue
        try:
            d = date(int(m["y"]), int(m["m"]), int(m["d"]))
        except ValueError:
            continue
        if d < today or d > today + timedelta(days=horizon_days):
            continue
        title = re.sub(r"\s+", " ", VARIANT.sub("", m["t"])).strip(" 　")
        if not title or (d, title) in seen:
            continue
        seen.add((d, title))
        rows.append((d, {"title": title}))
    rows.sort(key=lambda r: (r[0], r[1]["title"]))
    return group_by_day(rows)


def fetch_prime():
    return parse_prime(get("https://anilike.jp/end/amazon-end/"))


# ---------------------------------------------------------------- ニコニコ(Nアニメ)
NICO_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s*[（(][^）)]*[）)]\s*(\d{1,2}:\d{2})\s*[-－–〜~]\s*(.+)")


def parse_nico_post(raw, pub):
    """週間一覧記事の「●アニメ生放送●」の行を取り出す。pub: 記事の公開日(年の推定用)"""
    lines = to_lines(raw)
    start = next((i for i, l in enumerate(lines) if "アニメ生放送●" in l), None)
    if start is None:
        return []
    rows = []
    for l in lines[start + 1:]:
        if "●" in l:
            break
        m = NICO_RE.match(l)
        if not m:
            continue
        mo, dd = int(m[1]), int(m[2])
        year = pub.year + (1 if mo < pub.month - 6 else 0)
        try:
            d = date(year, mo, dd)
        except ValueError:
            continue
        rows.append((d, {"time": m[3], "title": m[4].strip()}))
    return rows


def fetch_nico():
    feed = ET.fromstring(get("https://blog.nicovideo.jp/niconews/index.xml").encode("utf-8"))
    posts = []
    for it in feed.iter("item"):
        title = it.findtext("title") or ""
        if "生放送一覧情報" in title and "Nアニメ" in title:
            try:
                pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(JST).date()
            except Exception:
                pub = TODAY
            posts.append((pub, (it.findtext("link") or "").strip()))
    posts.sort(reverse=True)
    rows = []
    for pub, link in posts[:3]:
        if link:
            rows += parse_nico_post(get(link), pub)
            time.sleep(1)
    rows = [r for r in rows if r[0] >= TODAY]
    rows.sort(key=lambda r: (r[0], r[1]["time"]))
    return group_by_day(rows)


# ---------------------------------------------------------------- 兵庫の地上波の映画
MOVIE = re.compile(r"映画[「『]|ロードショー|劇場版|シネマ|映画$|^映画")
NOT_MOVIE = re.compile(r"映画の話|シネマトリップ|映画情報|映画紹介|映画コーナー|映画ニュース|^見逃し")
ANCHOR = re.compile(r'(?is)<a[^>]+href="(/tv_events/[^"?#]+)[^"]*"[^>]*>(.*?)</a>')
MARKS = re.compile(r"[\U0001F200-\U0001F2FF]")  # 🈑🈔 などの放送マーク


def parse_epg_movies(raw):
    found, seen = [], set()
    for href, inner in ANCHOR.findall(raw):
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        if not text or not MOVIE.search(text) or NOT_MOVIE.search(text):
            continue
        title = MARKS.split(text)[0].strip()
        if len(title) > 60:
            title = title[:58] + "…"
        if title in seen:
            continue
        seen.add(title)
        found.append({"title": title, "link": "https://bangumi.org" + href})
    return found


# 放送枠がこの分数より短い「映画」は、予告・紹介コーナーとみなして除く
MIN_MINUTES = 80
DETAIL = re.compile(r"(\d{1,2})月(\d{1,2})日\s*[月火水木金土日]曜日?\s*(\d{1,2}):(\d{2})\s*[-－–〜~]\s*(\d{1,2}):(\d{2})\s*(\S*)")
_detail_cache = {}


def parse_detail(raw):
    """番組詳細ページから 放送日・開始/終了・枠の長さ・局名 を取り出す。取れなければ None"""
    m = DETAIL.search(" ".join(to_lines(raw)))
    if not m:
        return None
    mo, dd, sh, sm, eh, em = (int(m[i]) for i in range(1, 7))
    minutes = (eh * 60 + em) - (sh * 60 + sm)
    if minutes <= 0:
        minutes += 24 * 60
    year = TODAY.year
    try:
        d = date(year, mo, dd)
        if (d - TODAY).days < -180:
            d = date(year + 1, mo, dd)
    except ValueError:
        return None
    return {"date": d, "start": f"{sh}:{sm:02d}", "end": f"{eh}:{em:02d}",
            "minutes": minutes, "sort": sh * 60 + sm, "channel": re.sub(r"\d+$", "", m[7])}


def fetch_detail(link):
    if link not in _detail_cache:
        try:
            _detail_cache[link] = parse_detail(get(link + "?overwrite_area=42"))  # 兵庫の番組表と同じ形のURL
        except Exception as e:
            print(f"[warn] detail {link}: {e}")
            _detail_cache[link] = None
        time.sleep(1)
    return _detail_cache[link]


def fetch_movies(days=8):
    rows, ok, seen = [], False, set()
    for i in range(days):
        grid_day = TODAY + timedelta(days=i)
        url = f"https://bangumi.org/epg/td?broad_cast_date={grid_day:%Y%m%d}&ggm_group_id=85"
        try:
            candidates = parse_epg_movies(get(url))
            ok = True
        except Exception as e:
            print(f"[warn] movie {grid_day}: {e}")
            candidates = []
        time.sleep(1)
        for item in candidates:
            info = fetch_detail(item["link"])
            if info is None:
                # 詳細が読めないときは落とさずに残す（日付は番組表の列の日付）
                rows.append((grid_day, 9999, item))
                continue
            if info["minutes"] < MIN_MINUTES:
                continue
            key = (info["date"], item["title"])
            if key in seen:
                continue
            seen.add(key)
            item["time"] = f"{info['start']}〜{info['end']} {info['channel']}".strip()
            rows.append((info["date"], info["sort"], item))
    if not ok:
        raise RuntimeError("番組表を取得できませんでした")
    rows.sort(key=lambda r: (r[0], r[1]))
    return group_by_day([(d, item) for d, _, item in rows])


# ---------------------------------------------------------------- TVer (見出しニュース)
def fetch_news(queries):
    seen, items = set(), []
    for q in queries:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": q, "hl": "ja", "gl": "JP", "ceid": "JP:ja"})
        root = ET.fromstring(get(url).encode("utf-8"))
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            try:
                dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(JST)
            except Exception:
                dt = None
            if title and link and title not in seen:
                seen.add(title)
                items.append({"title": title, "link": link,
                              "date": dt.strftime("%m/%d") if dt else "", "_dt": dt.isoformat() if dt else ""})
    items.sort(key=lambda x: x["_dt"], reverse=True)
    for x in items:
        del x["_dt"]
    return items[:15]


# ---------------------------------------------------------------- 新着の検出と通知
# 新着のうち、タイトルにこれらの語を含むものだけスマホに通知する
NOTIFY_WORDS = re.compile(r"名作|傑作|懐かし|過去作|一挙|期間限定|特集|再配信|あの")
NEW_DAYS = 3  # この日数以内に初めて見つかったものに NEW を付ける


def notify(title, message, click=None):
    """ntfy.sh へ通知。環境変数 NTFY_TOPIC が無ければ何もしない。"""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return
    body = {"topic": topic, "title": title, "message": message}
    if click:
        body["click"] = click
    req = urllib.request.Request(
        "https://ntfy.sh/", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        print(f"[warn] notify: {e}")


def mark_new(items, old_items):
    """items に first_seen と new を付ける。戻り値: 今回はじめて見つかった項目"""
    first_run = not old_items
    old_seen = {i["title"]: i.get("first_seen", "2000-01-01") for i in old_items}
    limit = (TODAY - timedelta(days=NEW_DAYS)).isoformat()
    fresh = []
    for it in items:
        if it["title"] in old_seen:
            it["first_seen"] = old_seen[it["title"]]
        elif first_run:
            it["first_seen"] = "2000-01-01"  # 初回は全部が新着にならないように
        else:
            it["first_seen"] = TODAY.isoformat()
            fresh.append(it)
        if it["first_seen"] >= limit:
            it["new"] = True
    return fresh


TABS = [
    {"id": "tver", "name": "TVer", "kind": "news",
     "fetch": lambda: fetch_news(["TVer 名作ドラマ 配信 when:30d", "TVer 特集 期間限定 無料配信 when:30d"]),
     "note": "TVerには追加予定の一覧がないため、関連ニュースの見出しを表示しています。",
     "links": [("TVerを開く", "https://tver.jp/")]},
    {"id": "movie", "name": "地上波の映画", "kind": "days", "fetch": fetch_movies,
     "note": "Gガイド(兵庫)から映画らしい番組を拾い、放送枠が80分以上のものだけ表示しています。日付は実際の放送日です(深夜は翌日の日付で出ます)。",
     "links": [("Gガイド 兵庫の番組表", "https://bangumi.org/epg/td?ggm_group_id=85"),
               ("カンテレ 映画オンエア", "https://www.ktv.jp/cinema_tv/")]},
    {"id": "nico", "name": "ニコニコ", "kind": "days", "fetch": fetch_nico,
     "note": "ニコニコインフォの週間一覧(アニメ生放送)から取得。一挙放送(ニコニコアニメスペシャル)は下のリンクで確認してください。",
     "links": [("Nアニメ 一挙放送の予定", "https://anime.nicovideo.jp/live/reserved-ikkyo.html"),
               ("ニコニコアニメスペシャル", "https://ch.nicovideo.jp/anime-sp/live")]},
    {"id": "prime", "name": "アマプラ終了", "kind": "days", "fetch": fetch_prime,
     "note": "出典: アニライク。見放題終了「予定」で、取り消されて継続になることがあります。字幕版・吹替版は1つにまとめています。",
     "links": [("元のリスト(アニライク)", "https://anilike.jp/end/amazon-end/")]},
]


def main():
    old = {}
    if OUT.exists():
        try:
            old = {t["id"]: t for t in json.loads(OUT.read_text("utf-8"))["tabs"]}
        except Exception:
            old = {}
    tabs = []
    to_notify = []
    for t in TABS:
        tab = {"id": t["id"], "name": t["name"], "kind": t["kind"], "note": t["note"],
               "links": [{"label": l, "url": u} for l, u in t["links"]]}
        key = "days" if t["kind"] == "days" else "items"
        try:
            tab[key] = t["fetch"]()
            tab["status"] = "ok"
            if t["kind"] == "news":
                fresh = mark_new(tab[key], old.get(t["id"], {}).get("items", []))
                to_notify += [i for i in fresh if NOTIFY_WORDS.search(i["title"])]
        except Exception as e:
            print(f"[warn] {t['id']}: {e}")
            tab[key] = old.get(t["id"], {}).get(key, [])
            tab["status"] = "error"
        if t["kind"] == "days" and not tab[key] and tab["status"] == "ok":
            tab["status"] = "empty"
        tabs.append(tab)
    data = {"updated": datetime.now(JST).strftime("%Y-%m-%d %H:%M"), "tabs": tabs}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    if to_notify:
        lines = [i["title"] for i in to_notify[:5]]
        notify(f"TVer 新着 {len(to_notify)}件", "\n".join(lines), to_notify[0]["link"])
    for t in tabs:
        n = sum(len(d["items"]) for d in t["days"]) if "days" in t else len(t["items"])
        print(t["id"], t["status"], n)


if __name__ == "__main__":
    main()
