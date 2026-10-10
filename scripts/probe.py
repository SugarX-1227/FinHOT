"""临时探测：美联储 RSS、行情资金面、日历类接口（开发调试用，完成后删除）。"""
import json, time
from datetime import datetime, timedelta, timezone
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
S = requests.Session(); S.headers["User-Agent"] = UA
BJ = timezone(timedelta(hours=8))
today = datetime.now(BJ)
d8 = today.strftime("%Y%m%d"); dash = today.strftime("%Y-%m-%d")
prev = [(today - timedelta(days=i)) for i in range(0, 8)]

def show(name, url, params=None, headers=None, n=1500):
    try:
        r = S.get(url, params=params, headers=headers or {}, timeout=20)
        print(f"\n######## {name} status={r.status_code} len={len(r.content)} ct={r.headers.get('content-type')} url={r.url[:200]}")
        print(r.text[:n])
        return r
    except Exception as e:
        print(f"\n######## {name} ERROR {e!r}")

# ---- 美联储
for u in ["https://www.federalreserve.gov/feeds/press_all.xml", "https://www.federalreserve.gov/feeds/press_monetary.xml",
          "https://www.federalreserve.gov/feeds/speeches.xml", "https://www.federalreserve.gov/feeds/feeds.htm",
          "https://www.federalreserve.gov/feeds/press_bcreg.xml"]:
    show("fed " + u.rsplit("/", 1)[1], u, n=400)

# ---- 涨停池等（东财 push2ex），往前找最近交易日
ZT = "https://push2ex.eastmoney.com/getTopicZTPool"
for d in prev:
    r = S.get(ZT, params={"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt", "Pageindex": "0", "pagesize": "500",
                          "sort": "fbt:asc", "date": d.strftime("%Y%m%d")}, timeout=20)
    j = r.json(); pool = ((j.get("data") or {}).get("pool")) or []
    print("ztpool", d.strftime("%Y%m%d"), r.status_code, len(pool))
    if pool:
        tdate = d.strftime("%Y%m%d"); print(json.dumps(pool[:2], ensure_ascii=False)); print("keys data", list(j["data"].keys())); break
for api in ["getTopicDTPool", "getTopicZBPool", "getYesterdayZTPool", "getTopicQSPool"]:
    show(api, f"https://push2ex.eastmoney.com/{api}", {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt", "Pageindex": "0",
         "pagesize": "3", "sort": "fbt:asc" if api != "getTopicDTPool" else "fund:asc", "date": tdate}, n=700)
show("zdfenbu", "https://push2ex.eastmoney.com/getTopicZDFenBu", {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt"}, n=600)

# ---- 板块
for nm, fs, fid in [("industry by pct", "m:90+t:2", "f3"), ("concept by pct", "m:90+t:3", "f3"), ("industry by inflow", "m:90+t:2", "f62")]:
    show("board " + nm, "https://push2.eastmoney.com/api/qt/clist/get",
         {"pn": "1", "pz": "5", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": fid, "fs": fs,
          "fields": "f12,f14,f2,f3,f62,f184,f104,f105,f128,f136,f140,f141,f207,f208"}, n=1200)

# ---- 指数与成交额
show("indices", "https://push2.eastmoney.com/api/qt/ulist.np/get",
     {"fltt": "2", "secids": "1.000001,0.399001,0.399006,1.000688,1.000300,0.899050,1.000016", "fields": "f12,f14,f2,f3,f4,f6,f104,f105,f106,f124"}, n=1500)
# ---- 沪深港通
show("kamt", "https://push2.eastmoney.com/api/qt/kamt/get", {"fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f56,f62,f63,f65,f66"}, n=1500)
show("kamt.kline", "https://push2his.eastmoney.com/api/qt/kamt.kline/get", {"fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f55,f56", "klt": "101", "lmt": "3"}, n=1500)
# ---- 周 K（周报用）
show("weekly kline", "https://push2his.eastmoney.com/api/qt/stock/kline/get",
     {"secid": "1.000001", "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56,f57", "klt": "102", "fqt": "1", "end": "20500101", "lmt": "3"}, n=800)

# ---- 东财数据中心：龙虎榜 / 新股 / 解禁
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
start = (today - timedelta(days=10)).strftime("%Y-%m-%d")
show("lhb", DC, {"sortColumns": "TRADE_DATE,BILLBOARD_NET_AMT", "sortTypes": "-1,-1", "pageSize": "3", "pageNumber": "1",
                 "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW", "columns": "ALL", "source": "WEB", "client": "WEB",
                 "filter": f"(TRADE_DATE>='{start}')"}, n=2500)
show("ipo", DC, {"sortColumns": "APPLY_DATE", "sortTypes": "-1", "pageSize": "3", "pageNumber": "1", "reportName": "RPTA_APP_IPOAPPLY",
                 "columns": "ALL", "source": "WEB", "client": "WEB"}, n=2500)
end = (today + timedelta(days=7)).strftime("%Y-%m-%d")
show("lift", DC, {"sortColumns": "FREE_DATE,LIFT_MARKET_CAP", "sortTypes": "1,-1", "pageSize": "3", "pageNumber": "1", "reportName": "RPT_LIFT_STAGE",
                  "columns": "ALL", "source": "WEB", "client": "WEB", "filter": f"(FREE_DATE>='{dash}')(FREE_DATE<='{end}')"}, n=2500)

# ---- 经济日历候选
y, m, dd = today.strftime("%Y"), today.strftime("%m"), today.strftime("%d")
for d in [today, today + timedelta(days=1), today + timedelta(days=3)]:
    y, m, dd = d.strftime("%Y"), d.strftime("%m"), d.strftime("%d")
    show(f"jin10 rili economics {d:%m%d}", f"https://cdn-rili.jin10.com/web_data/{y}/daily/{m}/{dd}/economics.json", n=1200)
show("jin10 rili event", f"https://cdn-rili.jin10.com/web_data/{y}/daily/{m}/{dd}/event.json", n=1200)
show("jin10 rili api", "https://e0430d16720e4211b5e072c26205c890.z3c.jin10.com/get/data", {"date": dash, "category": "cj"},
     {"x-app-id": "sKKYe29sFuJaeOCJ", "x-version": "2.0", "Referer": "https://rili.jin10.com/", "Origin": "https://rili.jin10.com"}, n=1200)
show("em calendar", DC, {"sortColumns": "PUBLISH_DATE", "sortTypes": "1", "pageSize": "3", "pageNumber": "1", "reportName": "RPT_ECONOMICCALENDAR",
                         "columns": "ALL", "source": "WEB", "client": "WEB"}, n=1000)
show("wscn calendar", "https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas",
     {"start": int((today - timedelta(hours=2)).timestamp()), "end": int((today + timedelta(days=1)).timestamp())}, n=1500)
show("wscn calendar2", "https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas",
     {"start": int((today + timedelta(days=3)).timestamp()), "end": int((today + timedelta(days=4)).timestamp())}, n=1500)
