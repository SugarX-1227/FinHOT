"""临时：抓取 aihot.news 线上站点的页面文字与截图（竞品调研用，完成后删除）。"""
import base64, sys
from playwright.sync_api import sync_playwright

BASE = "https://aihot.news"
TEXT_PAGES = ["/llms.txt", "/api/v1/agent", "/", "/hot", "/all", "/topics", "/daily", "/weekly", "/about", "/agent"]
SHOTS = [("/", 1280, 2400, False), ("/", 390, 2200, True), ("/hot", 1280, 2000, False), ("/daily", 1280, 2600, False)]

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(locale="zh-CN", viewport={"width": 1280, "height": 1800})
    pg = ctx.new_page()
    links = set()
    for path in TEXT_PAGES:
        try:
            pg.goto(BASE + path, wait_until="networkidle", timeout=45000)
            txt = pg.inner_text("body")
            print(f"\n######## PAGE {path} url={pg.url} len={len(txt)}")
            print(txt[:7000])
            for a in pg.query_selector_all("a[href]")[:400]:
                h = a.get_attribute("href") or ""
                if h.startswith("/") and not h.startswith("//"):
                    links.add(h.split("?")[0])
        except Exception as e:
            print(f"\n######## PAGE {path} ERROR {e}")
    print("\n######## LINKS", sorted(links)[:200])
    # 一个事件页、一个主题页、一个条目页
    for prefix in ("/story/", "/topic/", "/item/"):
        cand = [l for l in sorted(links) if l.startswith(prefix)]
        if cand:
            try:
                pg.goto(BASE + cand[0], wait_until="networkidle", timeout=45000)
                txt = pg.inner_text("body")
                print(f"\n######## PAGE {cand[0]} len={len(txt)}")
                print(txt[:5000])
                SHOTS.append((cand[0], 1280, 2200, False))
            except Exception as e:
                print(f"\n######## PAGE {cand[0]} ERROR {e}")
    for path, w, h, mobile in SHOTS:
        try:
            c = b.new_context(locale="zh-CN", viewport={"width": w, "height": h}, is_mobile=mobile, device_scale_factor=1)
            q = c.new_page()
            q.goto(BASE + path, wait_until="networkidle", timeout=45000)
            q.wait_for_timeout(1500)
            img = q.screenshot(type="jpeg", quality=55, full_page=False)
            data = base64.b64encode(img).decode()
            tag = f"{path.strip('/').replace('/', '_') or 'home'}_{w}"
            print(f"\nSHOT {tag} {len(data)}")
            for i in range(0, len(data), 20000):
                print(f"B64|{tag}|{data[i:i+20000]}")
            c.close()
        except Exception as e:
            print(f"\nSHOT {path} ERROR {e}")
    b.close()
