"""临时探测脚本 v2：财联社备选接口 + 各源翻页能力。"""
import hashlib, json, re, time, urllib.parse
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
S = requests.Session(); S.headers["User-Agent"] = UA

def sign(params):
    q = urllib.parse.urlencode(sorted(params.items()))
    return hashlib.md5(hashlib.sha1(q.encode()).hexdigest().encode()).hexdigest()

def show(name, r, n=800):
    print(f"\n######## {name} status={r.status_code} len={len(r.content)} ct={r.headers.get('content-type')}")
    print(r.text[:n])

now = int(time.time())
H = {"Referer": "https://www.cls.cn/telegraph"}
for base in ["https://www.cls.cn/v1/roll/get_roll_list", "https://www.cls.cn/api/roll/get_roll_list", "https://api3.cls.cn/v1/roll/get_roll_list", "https://www.cls.cn/nodeapi/telegraphs"]:
    p = {"app": "CailianpressWeb", "category": "", "last_time": str(now), "os": "web", "refresh_type": "1", "rn": "20", "sv": "8.4.6"}
    p["sign"] = sign(p)
    try: show("cls " + base, S.get(base, params=p, headers=H, timeout=20))
    except Exception as e: print("ERR", base, e)
try:
    r = S.get("https://www.cls.cn/telegraph", timeout=20)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    print("\n######## cls telegraph page", r.status_code, len(r.text), "next_data:", bool(m))
    if m:
        d = json.loads(m.group(1)); s = json.dumps(d, ensure_ascii=False)
        print(s[:3000])
    for u in sorted(set(re.findall(r'["\'](/(?:api|v1|v2|nodeapi)[^"\']{3,80})["\']', r.text)))[:40]: print("path:", u)
    for js in re.findall(r'src="(/_next/static/chunks/pages/[^"]+)"', r.text)[:5]:
        t = S.get("https://www.cls.cn" + js, timeout=20).text
        print("js", js, sorted(set(re.findall(r'["\'`](/(?:api|v1|v2|v3|nodeapi)/[A-Za-z0-9_/\-]{3,80})', t)))[:40])
except Exception as e: print("ERR page", e)

def pages(name, fn, n=3):
    try:
        tot = 0; state = None
        for i in range(n):
            items, state, first, last = fn(state)
            tot += len(items)
            print(f"## {name} page{i+1}: n={len(items)} first={first} last={last} next={state}")
            if not items: break
            time.sleep(0.5)
        print(f"## {name} total={tot}")
    except Exception as e:
        print(f"## {name} ERROR {e!r}")

def wscn(cur):
    p = {"channel": "global-channel", "client": "pc", "limit": "100"}
    if cur: p["cursor"] = cur
    d = S.get("https://api-one-wscn.awtmt.com/apiv1/content/lives", params=p, timeout=20).json()["data"]
    it = d["items"]; return it, d.get("next_cursor"), it[0]["display_time"] if it else None, it[-1]["display_time"] if it else None
def jin10(mt):
    p = {"channel": "-8200", "vip": "1"}
    if mt: p["max_time"] = mt
    it = S.get("https://flash-api.jin10.com/get_flash_list", params=p, headers={"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0"}, timeout=20).json()["data"]
    return it, it[-1]["time"] if it else None, it[0]["time"] if it else None, it[-1]["time"] if it else None
def em(se):
    p = {"client": "web", "biz": "web_724", "fastColumn": "102", "sortEnd": se or "", "pageSize": "200", "req_trace": str(int(time.time()*1000))}
    d = S.get("https://np-weblist.eastmoney.com/comm/web/getFastNewsList", params=p, timeout=20).json()["data"]
    it = d["fastNewsList"]; return it, d.get("sortEnd"), it[0]["showTime"] if it else None, it[-1]["showTime"] if it else None
def sina(pg):
    pg = (pg or 0) + 1
    p = {"page": str(pg), "page_size": "100", "zhibo_id": "152", "tag_id": "0", "dire": "f", "dpc": "1", "type": "0"}
    it = S.get("https://zhibo.sina.com.cn/api/zhibo/feed", params=p, timeout=20).json()["result"]["data"]["feed"]["list"]
    return it, pg, it[0]["create_time"] if it else None, it[-1]["create_time"] if it else None
def ths(pg):
    pg = (pg or 0) + 1
    p = {"page": str(pg), "tag": "", "track": "website", "pagesize": "100"}
    it = S.get("https://news.10jqka.com.cn/tapp/news/push/stock/", params=p, timeout=20).json()["data"]["list"]
    return it, pg, it[0]["ctime"] if it else None, it[-1]["ctime"] if it else None
def stcn(pt):
    p = {"page_time": pt} if pt else {}
    it = S.get("https://www.stcn.com/article/list/kx.html", params=p, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=20).json()["data"]
    return it, it[-1].get("pageTime") if it else None, it[0]["show_time"] if it else None, it[-1]["show_time"] if it else None
def yicai(pg):
    pg = (pg or 0) + 1
    it = S.get(f"https://www.yicai.com/api/ajax/getbrieflist?page={pg}&pagesize=100", headers={"Referer": "https://www.yicai.com/brief/"}, timeout=20).json()
    return it, pg, it[0]["CreateDate"] if it else None, it[-1]["CreateDate"] if it else None

for name, fn in [("wscn", wscn), ("jin10", jin10), ("em", em), ("sina", sina), ("ths", ths), ("stcn", stcn), ("yicai", yicai)]:
    pages(name, fn)

# 字段样例
it = S.get("https://news.10jqka.com.cn/tapp/news/push/stock/", params={"page": "1", "pagesize": "30"}, timeout=20).json()["data"]["list"]
print("ths colors", [(x["color"], x.get("import"), x["title"][:15]) for x in it])
it = S.get("https://www.yicai.com/api/ajax/getbrieflist?page=1&pagesize=30", timeout=20).json()
print("yicai important", sum(1 for x in it if x.get("IsImportant")), len(it))
d = S.get("https://www.stcn.com/article/list/kx.html", headers={"X-Requested-With": "XMLHttpRequest"}, timeout=20).json()["data"]
print("stcn keys", list(d[0].keys()), [(x.get("isRed"), x.get("red")) for x in d][:30])
