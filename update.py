#!/usr/bin/env python3
"""TV/配信ウォッチ: Google ニュースのRSSから見出しを集めて data.json を作る。
標準ライブラリだけで動く。GitHub Actions から毎日実行される想定。
"""
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

JST = timezone(timedelta(hours=9))
OUT = Path(__file__).with_name("data.json")
MAX_ITEMS = 15

# 検索語は自由に追加・変更してよい。"when:14d" は直近14日の記事に絞る指定。
TABS = [
    {
        "id": "tver",
        "name": "TVer",
        "queries": ["TVer 名作ドラマ 配信 when:30d", "TVer 特集 期間限定 無料配信 when:30d"],
        "links": [("TVerを開く", "https://tver.jp/")],
    },
    {
        "id": "movie",
        "name": "地上波の映画",
        "queries": ["金曜ロードショー 放送 when:14d", "関西テレビ 映画 放送 when:14d", "読売テレビ 映画 放送 when:14d"],
        "links": [
            ("Gガイド 兵庫の番組表", "https://bangumi.org/epg/td?ggm_group_id=85"),
            ("カンテレ 映画オンエア", "https://www.ktv.jp/cinema_tv/"),
        ],
    },
    {
        "id": "nico",
        "name": "ニコニコ",
        "queries": ["ニコニコアニメスペシャル 一挙放送 when:30d", "Nアニメ 生放送 上映会 when:14d"],
        "links": [
            ("Nアニメ 一挙放送の予定", "https://anime.nicovideo.jp/live/reserved-ikkyo.html"),
            ("ニコニコアニメスペシャル 生放送", "https://ch.nicovideo.jp/anime-sp/live"),
        ],
    },
    {
        "id": "prime",
        "name": "アマプラ終了",
        "queries": ["プライムビデオ 見放題 配信終了 when:30d", "Prime Video 配信終了予定 when:30d"],
        "links": [
            ("見放題終了予定リスト(anilike)", "https://anilike.jp/end/amazon-end/"),
            ("終了・新作カレンダー(うさぎ亭)", "https://usagitei.com/prime-video-calendar/"),
        ],
    },
]


def fetch_rss(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (tv-watch personal use)"})
    with urllib.request.urlopen(req, timeout=30) as res:
        root = ET.fromstring(res.read())
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(JST)
        except Exception:
            dt = None
        if title and link:
            items.append({"title": title, "link": link, "source": src,
                          "date": dt.isoformat() if dt else ""})
    return items


def main():
    old = {}
    if OUT.exists():
        try:
            old = {t["id"]: t for t in json.loads(OUT.read_text("utf-8"))["tabs"]}
        except Exception:
            old = {}

    tabs = []
    for tab in TABS:
        seen, items, ok = set(), [], False
        for q in tab["queries"]:
            try:
                for item in fetch_rss(q):
                    if item["title"] not in seen:
                        seen.add(item["title"])
                        items.append(item)
                ok = True
            except Exception as e:  # 1つ失敗しても続ける
                print(f"[warn] {tab['id']} / {q}: {e}")
        if not ok and tab["id"] in old:
            items = old[tab["id"]]["items"]  # 全滅なら前回分を残す
        items.sort(key=lambda x: x["date"], reverse=True)
        tabs.append({
            "id": tab["id"],
            "name": tab["name"],
            "links": [{"label": l, "url": u} for l, u in tab["links"]],
            "items": items[:MAX_ITEMS],
        })

    data = {"updated": datetime.now(JST).strftime("%Y-%m-%d %H:%M"), "tabs": tabs}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    print("wrote", OUT, sum(len(t["items"]) for t in tabs), "items")


if __name__ == "__main__":
    main()
