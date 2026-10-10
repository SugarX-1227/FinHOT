"""临时探测 v2：财经日历字段、周 K 线。"""
import collections, json, time
from datetime import datetime, timedelta, timezone
import requests
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"
BJ = timezone(timedelta(hours=8)); now = datetime.now(BJ)
start = datetime(2026, 10, 12, tzinfo=BJ); end = start + timedelta(days=5)
d = S.get("https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas", params={"start": int(start.timestamp()), "end": int(end.timestamp())}, timeout=20).json()
items = d["data"]["items"]
print("wscn n", len(items), collections.Counter((i["calendar_type"], i["importance"]) for i in items))
for t in ("FD", "FE", "MD", "CE"):
    ex = [i for i in items if i["calendar_type"] == t][:2]
    for e in ex: print("wscn", t, json.dumps({k: e[k] for k in ("public_date", "country", "title", "importance", "actual", "forecast", "previous", "unit", "period", "calendar_type", "event")}, ensure_ascii=False))
print("types", sorted({i["calendar_type"] for i in items}))
for i in sorted(items, key=lambda x: (-x["importance"], x["public_date"]))[:25]:
    print("  ", datetime.fromtimestamp(i["public_date"], BJ).strftime("%m-%d %H:%M"), i["importance"], i["calendar_type"], i["country"], i["title"][:50], i["forecast"], i["previous"])
H = {"x-app-id": "sKKYe29sFuJaeOCJ", "x-version": "2.0", "Referer": "https://rili.jin10.com/", "Origin": "https://rili.jin10.com"}
for cat in ("cj", "sj", "event", "hd", "jq"):
    r = S.get("https://e0430d16720e4211b5e072c26205c890.z3c.jin10.com/get/data", params={"date": "2026-10-13", "category": cat}, headers=H, timeout=20)
    print("jin10", cat, r.status_code, r.text[:300])
for path in ("get/event", "get/events", "get/holiday"):
    r = S.get(f"https://e0430d16720e4211b5e072c26205c890.z3c.jin10.com/{path}", params={"date": "2026-10-13"}, headers=H, timeout=20)
    print("jin10", path, r.status_code, r.text[:300])
for i in range(3):
    try:
        r = S.get("https://push2his.eastmoney.com/api/qt/stock/kline/get", params={"secid": "1.000001", "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56,f57,f59", "klt": "102", "fqt": "1", "end": "20500101", "lmt": "3", "ut": "fa5fd1943c7b386f172d6893dbfba10b"}, timeout=20)
        print("kline", r.status_code, r.text[:600]); break
    except Exception as e:
        print("kline err", e); time.sleep(2)
