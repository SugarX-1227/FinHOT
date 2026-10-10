"""临时探测 v3：板块列表接口在海外服务器上的可用性。"""
import time, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
F = "f12,f14,f3,f62,f104,f105,f128,f136"
variants = {
    "pz20": ("https://push2.eastmoney.com/api/qt/clist/get", {"pn": 1, "pz": 20, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90+t:2", "fields": F}, {}),
    "pz100+ut+ref": ("https://push2.eastmoney.com/api/qt/clist/get", {"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90+t:2", "fields": F, "ut": "bd1d9ddb04089700cf9c27f6f7426281"}, {"Referer": "https://quote.eastmoney.com/center/gridlist.html"}),
    "pz50+ut": ("https://push2.eastmoney.com/api/qt/clist/get", {"pn": 1, "pz": 50, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90+t:3", "fields": F, "ut": "bd1d9ddb04089700cf9c27f6f7426281"}, {"Referer": "https://quote.eastmoney.com/center/gridlist.html"}),
    "delay-pz100": ("https://push2delay.eastmoney.com/api/qt/clist/get", {"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90+t:2", "fields": F, "ut": "bd1d9ddb04089700cf9c27f6f7426281"}, {}),
    "http-pz100": ("http://push2.eastmoney.com/api/qt/clist/get", {"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90+t:2", "fields": F}, {}),
    "fflow": ("https://push2.eastmoney.com/api/qt/clist/get", {"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f62", "fs": "m:90+t:2", "fields": "f12,f14,f2,f3,f62,f184,f204,f205", "ut": "b2884a393a59ad64002292a3e90d46a5"}, {"Referer": "https://data.eastmoney.com/bkzj/hy.html"}),
    "sina-hy": ("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php", {}, {"Referer": "https://finance.sina.com.cn/"}),
    "sina-fl": ("https://money.finance.sina.com.cn/q/view/newFLJK.php", {"param": "class"}, {"Referer": "https://finance.sina.com.cn/"}),
}
for name, (url, params, headers) in variants.items():
    for attempt in range(3):
        try:
            s = requests.Session(); s.headers["User-Agent"] = UA
            r = s.get(url, params=params, headers=headers, timeout=15)
            body = r.content.decode(r.encoding or "gbk", "replace") if "sina" in name else r.text
            print(f"{name} #{attempt} status={r.status_code} len={len(r.content)} final={r.url[:60]} :: {body[:240]!r}")
        except Exception as e:
            print(f"{name} #{attempt} ERR {type(e).__name__}: {str(e)[:120]}")
        time.sleep(1)
