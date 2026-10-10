# -*- coding: utf-8 -*-
"""布局调整后的主界面渲染：三栏数据态 + 左栏问题视图"""
import os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs")
URL = BASE + "index.html"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1680, "height": 1000}, device_scale_factor=1)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL, wait_until="networkidle")
    pg.fill("#loginUser", "admin"); pg.fill("#loginPwd", "admin"); pg.click(".btn-login")
    pg.wait_for_timeout(1200)

    pg.evaluate("""() => {
      API_STATE.dbId='demo01'; API_STATE.dbName='demo01.db';
      API_STATE.online=true; saveApiState();
    }""")
    pg.evaluate("() => renderWorkspace()")
    pg.wait_for_selector("#onlineGrid .t-row", timeout=20000)
    pg.evaluate("""() => {
      const by=(WS.review&&WS.review.by_hole)||{};
      const k=Object.keys(by).find(k=>by[k].h||by[k].m);
      if(k) wsSelectHole(k);
    }""")
    pg.wait_for_timeout(1200)
    pg.screenshot(path=os.path.join(OUT, "layout-workspace.png"))

    # 左栏切到「问题」视图
    pg.evaluate("() => wsSwitchView('issues')")
    pg.wait_for_timeout(600)
    pg.screenshot(path=os.path.join(OUT, "layout-issues.png"))

    if errs:
        print("PAGE ERRORS:")
        for e in errs[:10]:
            print(" ", e)
    b.close()
print("done")
