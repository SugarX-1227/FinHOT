"""离线测试用的接口样例（取自 2026-10 各接口真实返回，已裁剪）。"""

ZT_POOL = {"data": {"tc": 4, "qdate": 20261009, "pool": [
    {"c": "002058", "n": "紫竹高科", "zdp": 9.99, "lbc": 4, "fbt": 92500, "fund": 89302489, "zbc": 0, "hybk": "电池",
     "zttj": {"days": 4, "ct": 4}},
    {"c": "600241", "n": "时代万恒", "zdp": 10.01, "lbc": 5, "fbt": 92500, "fund": 229379199, "zbc": 0, "hybk": "电池",
     "zttj": {"days": 5, "ct": 5}},
    {"c": "300413", "n": "芒果超媒", "zdp": 19.98, "lbc": 1, "fbt": 101500, "fund": 50000000, "zbc": 1, "hybk": "视频媒体",
     "zttj": {"days": 1, "ct": 1}},
    {"c": "301511", "n": "德福科技", "zdp": 20.0, "lbc": 1, "fbt": 133000, "fund": 10000000, "zbc": 0, "hybk": "电池",
     "zttj": {"days": 1, "ct": 1}},
]}}
FENBU = {"data": {"qdate": 20261009, "fenbu": [{"-1": 740}, {"-10": 6}, {"0": 103}, {"1": 970}, {"11": 69}, {"-11": 8}]}}
BOARDS_IND = {"data": {"total": 3, "diff": [
    {"f12": "BK1296", "f14": "视频媒体", "f3": 19.98, "f62": 268003552.0, "f104": 1, "f105": 0, "f128": "芒果超媒", "f136": 19.98},
    {"f12": "BK1033", "f14": "电池", "f3": 2.23, "f62": 5043652096.0, "f104": 78, "f105": 29, "f128": "紫竹高科", "f136": 9.99},
    {"f12": "BK0475", "f14": "银行", "f3": -1.2, "f62": -3000000000.0, "f104": 3, "f105": 39, "f128": "成都银行", "f136": 0.5},
]}}
BOARDS_CON = {"data": {"total": 2, "diff": [
    {"f12": "BK1645", "f14": "昨日打二板以上表现", "f3": 6.77, "f62": -1.0, "f104": 7, "f105": 1, "f128": "x", "f136": 9.9},
    {"f12": "BK1172", "f14": "AI语料", "f3": 6.07, "f62": 1628981392.0, "f104": 36, "f105": 1, "f128": "新华传媒", "f136": 10.01},
]}}
INDICES = {"data": {"diff": [
    {"f2": 3813.79, "f3": 0.05, "f6": 888561198263.9, "f12": "000001", "f14": "上证指数"},
    {"f2": 12641.86, "f3": 0.17, "f6": 1011994216302.37, "f12": "399001", "f14": "深证成指"},
    {"f2": 3043.33, "f3": 0.22, "f6": 494823547448.06, "f12": "399006", "f14": "创业板指"},
    {"f2": 1037.31, "f3": 2.49, "f6": 16326814935.0, "f12": "899050", "f14": "北证50"},
]}}
KAMT = {"data": {
    "hk2sh": {"date2": "2026-10-09", "buySellAmt": 14168318.65, "netBuyAmt": 0.0},
    "hk2sz": {"date2": "2026-10-09", "buySellAmt": 15505216.6, "netBuyAmt": 0.0},
    "sh2hk": {"buySellAmt": 5038844.98, "netBuyAmt": -233086.41},
    "sz2hk": {"buySellAmt": 2855299.45, "netBuyAmt": 262153.64}}}
LHB = {"result": {"data": [
    {"TRADE_DATE": "2026-10-09 00:00:00", "SECURITY_CODE": "300821", "SECURITY_NAME_ABBR": "东岳硅材", "CHANGE_RATE": 20.014,
     "BILLBOARD_NET_AMT": 187582294.39, "EXPLAIN": "2家机构买入，成功率41.23%", "EXPLANATION": "日涨幅达到15%的前5只证券"},
    {"TRADE_DATE": "2026-10-09 00:00:00", "SECURITY_CODE": "300821", "SECURITY_NAME_ABBR": "东岳硅材", "CHANGE_RATE": 20.014,
     "BILLBOARD_NET_AMT": 187582294.39, "EXPLAIN": "重复", "EXPLANATION": "连续三日"},
    {"TRADE_DATE": "2026-10-09 00:00:00", "SECURITY_CODE": "000420", "SECURITY_NAME_ABBR": "吉林化纤", "CHANGE_RATE": -10.1,
     "BILLBOARD_NET_AMT": -78378901.12, "EXPLAIN": "", "EXPLANATION": "日跌幅偏离值达到7%"},
    {"TRADE_DATE": "2026-10-08 00:00:00", "SECURITY_CODE": "600000", "SECURITY_NAME_ABBR": "旧数据", "CHANGE_RATE": 1,
     "BILLBOARD_NET_AMT": 999999999, "EXPLAIN": "", "EXPLANATION": ""},
]}}
WSCN_CAL = {"code": 20000, "data": {"items": [
    {"public_date": 1792027800, "country": "中国", "title": "9月CPI同比", "importance": 4, "calendar_type": "FD",
     "actual": "", "forecast": "", "previous": "0.8", "unit": "%"},
    {"public_date": 1792049400, "country": "美国", "title": "9月CPI同比", "importance": 4, "calendar_type": "FD",
     "actual": "", "forecast": "3.6", "previous": "3.4", "unit": "%"},
    {"public_date": 1792036920, "country": "中国", "title": "2026世界储能大会", "importance": 3, "calendar_type": "FE",
     "foresight": "前瞻 | 大会将发布……"},
    {"public_date": 1792027800, "country": "英国", "title": "BRC同店销售同比", "importance": 1, "calendar_type": "FD",
     "previous": "0.5", "unit": "%"},
]}}
IPO = {"result": {"data": [{"SECURITY_CODE": "001381", "SECURITY_NAME": "皇冠新材", "APPLY_CODE": "001381",
                            "MARKET": "深交所主板", "ISSUE_PRICE": None, "ONLINE_APPLY_UPPER": 13500,
                            "MAIN_BUSINESS": "以功能性新材料为核心,研发、生产及销售工业级胶粘材料"}]}}
LIFTS = {"result": {"data": [
    {"SECURITY_CODE": "001286", "SECURITY_NAME_ABBR": "陕西能源", "FREE_DATE": "2026-10-12 00:00:00",
     "LIFT_MARKET_CAP": 2385600, "FREE_RATIO": 1.7778, "FREE_SHARES_TYPE": "追加承诺限售股份上市流通"},
    {"SECURITY_CODE": "688197", "SECURITY_NAME_ABBR": "首药控股", "FREE_DATE": "2026-10-12 00:00:00",
     "LIFT_MARKET_CAP": 300821.05, "FREE_RATIO": 1.3236, "FREE_SHARES_TYPE": "追加承诺限售股份上市流通"},
]}}
