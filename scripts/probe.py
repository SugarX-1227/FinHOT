"""临时：把 24 小时原始条目压缩后打印到日志，供本地校准打分规则。"""
import base64, gzip, json, logging
from datetime import timedelta
from finhot.collect import collect
from finhot.config import load_sources
from finhot.timeutil import now

logging.basicConfig(level=logging.WARNING)
res = collect(load_sources()["sources"], now() - timedelta(hours=24))
rows = [it.to_dict(max_content=300) for r in res for it in r.items]
blob = base64.b64encode(gzip.compress(json.dumps(rows, ensure_ascii=False).encode())).decode()
print("ITEMS", len(rows), "B64LEN", len(blob))
for i in range(0, len(blob), 20000):
    print("B64:" + blob[i:i + 20000])
print("END")
