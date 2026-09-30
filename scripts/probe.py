"""临时探测脚本 v3：证券时报 / 第一财经翻页参数。"""
import requests
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"
H = {"X-Requested-With": "XMLHttpRequest", "Referer": "https://www.stcn.com/article/list/kx.html"}
first = S.get("https://www.stcn.com/article/list/kx.html", headers=H, timeout=20).json()["data"]
last = first[-1]
print("stcn first page", len(first), last["id"], last["pageTime"], last["show_time"])
for params in [{"page_time": last["pageTime"]}, {"type": "kx", "page_time": last["pageTime"]}, {"last_time": last["show_time"]},
               {"page": 2}, {"p": 2}, {"page_time": last["show_time"]}, {"pageTime": last["pageTime"]}, {"last_id": last["id"]}]:
    try:
        r = S.get("https://www.stcn.com/article/list/kx.html", params=params, headers=H, timeout=20)
        d = r.json().get("data") or []
        print("stcn", params, r.status_code, len(d), d[0]["show_time"] if d else None, d[0]["id"] if d else None)
    except Exception as e:
        print("stcn", params, "ERR", e, r.text[:200])
for size in (30, 50, 100):
    r = S.get(f"https://www.yicai.com/api/ajax/getbrieflist?page=2&pagesize={size}", headers={"Referer": "https://www.yicai.com/brief/"}, timeout=20)
    j = r.json()
    print("yicai", size, type(j).__name__, len(j) if isinstance(j, list) else str(j)[:200], j[0]["CreateDate"] if isinstance(j, list) and j else None)
