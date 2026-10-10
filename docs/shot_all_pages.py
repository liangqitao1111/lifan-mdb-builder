# -*- coding: utf-8 -*-
"""全站四个路由页统一风格渲染"""
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

    # 工作台
    pg.evaluate("() => route('workspace')")
    pg.wait_for_selector("#onlineGrid .t-row", timeout=20000)
    pg.evaluate("""() => {
      const by=(WS.review&&WS.review.by_hole)||{};
      const k=Object.keys(by).find(k=>by[k].h||by[k].m);
      if(k) wsSelectHole(k);
    }""")
    pg.wait_for_timeout(1000)
    pg.screenshot(path=os.path.join(OUT, "page-workspace.png"))

    # 修正与成果
    pg.evaluate("() => route('tools')")
    pg.wait_for_timeout(900)
    pg.screenshot(path=os.path.join(OUT, "page-tools.png"))

    # 生成 MDB
    pg.evaluate("() => route('build')")
    pg.wait_for_timeout(900)
    pg.screenshot(path=os.path.join(OUT, "page-build.png"))

    # 参数中心
    pg.evaluate("() => route('config')")
    pg.wait_for_timeout(2500)
    pg.screenshot(path=os.path.join(OUT, "page-config.png"))

    if errs:
        print("PAGE ERRORS:")
        for e in errs[:10]:
            print(" ", e)
    b.close()
print("done")
