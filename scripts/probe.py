"""临时探测脚本：打印各候选信源接口的真实返回结构（仅用于开发调试）。"""
import hashlib, json, time, urllib.parse
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

def cls_sign(params):
    q = urllib.parse.urlencode(sorted(params.items()))
    return hashlib.md5(hashlib.sha1(q.encode()).hexdigest().encode()).hexdigest()

now = int(time.time())
p_cls = {"app": "CailianpressWeb", "category": "", "lastTime": str(now), "last_time": str(now), "os": "web", "refresh_type": "1", "rn": "20", "sv": "7.7.5"}
p_cls["sign"] = cls_sign(p_cls)
p_cls2 = {"app": "CailianpressWeb", "os": "web", "sv": "8.4.6", "rn": "20"}
p_cls2["sign"] = cls_sign(p_cls2)

PROBES = [
    ("cls", "https://www.cls.cn/nodeapi/telegraphList", p_cls, {"Referer": "https://www.cls.cn/telegraph"}),
    ("cls2", "https://www.cls.cn/nodeapi/updateTelegraphList", p_cls2, {"Referer": "https://www.cls.cn/telegraph"}),
    ("wscn", "https://api-one-wscn.awtmt.com/apiv1/content/lives", {"channel": "global-channel", "client": "pc", "limit": "3"}, {}),
    ("jin10", "https://flash-api.jin10.com/get_flash_list", {"channel": "-8200", "vip": "1"}, {"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0", "Referer": "https://www.jin10.com/", "Origin": "https://www.jin10.com"}),
    ("eastmoney", "https://np-weblist.eastmoney.com/comm/web/getFastNewsList", {"client": "web", "biz": "web_724", "fastColumn": "102", "sortEnd": "", "pageSize": "3", "req_trace": str(now)}, {"Referer": "https://kuaixun.eastmoney.com/"}),
    ("sina", "https://zhibo.sina.com.cn/api/zhibo/feed", {"page": "1", "page_size": "3", "zhibo_id": "152", "tag_id": "0", "dire": "f", "dpc": "1", "type": "0"}, {"Referer": "https://finance.sina.com.cn/7x24/"}),
    ("ths", "https://news.10jqka.com.cn/tapp/news/push/stock/", {"page": "1", "tag": "", "track": "website", "pagesize": "3"}, {"Referer": "https://news.10jqka.com.cn/realtimenews.html"}),
    ("govcn", "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json", {}, {}),
    ("em_quote", "https://push2.eastmoney.com/api/qt/ulist.np/get", {"fltt": "2", "secids": "100.DJIA,100.SPX,100.NDX,100.HXC,100.HSI,100.UDI,133.USDCNH,101.GC00Y,102.CL00Y,171.US10Y,104.CN00Y,100.N225", "fields": "f12,f13,f14,f2,f3,f4,f124"}, {"Referer": "https://quote.eastmoney.com/"}),
    ("cnbc", "https://www.cnbc.com/id/100003114/device/rss/rss.html", {}, {}),
    ("fed", "https://www.federalreserve.gov/feeds/press_all.xml", {}, {}),
    ("mw", "https://feeds.content.dowjones.io/public/rss/mw_topstories", {}, {}),
    ("wsj", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", {}, {}),
    ("csrc", "http://www.csrc.gov.cn/csrc/c100028/common_list.shtml", {}, {}),
    ("pbc", "http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html", {}, {}),
    ("stcn", "https://www.stcn.com/article/list/kx.html", {}, {"X-Requested-With": "XMLHttpRequest"}),
    ("yicai", "https://www.yicai.com/api/ajax/getbrieflist?page=1&pagesize=3", {}, {"Referer": "https://www.yicai.com/brief/"}),
]

for name, url, params, headers in PROBES:
    print(f"\n######## {name} {url}")
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA, **headers}, timeout=20)
        print("status", r.status_code, "len", len(r.content), "ctype", r.headers.get("content-type"))
        r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
        print(r.text[:2500])
    except Exception as e:
        print("ERROR", repr(e))
