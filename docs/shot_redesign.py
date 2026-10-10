# -*- coding: utf-8 -*-
"""换皮后真实渲染截图：登录页 / 工作台空态 / 三栏数据态"""
import os, sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "sample", "lizheng_review_sample.lz")
OUT = os.path.join(ROOT, "docs")
URL = BASE + "index.html"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1680, "height": 1000}, device_scale_factor=1)
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(500)

    # 1. 登录页（未登录状态）
    pg.screenshot(path=os.path.join(OUT, "skin-login.png"))

    # 2. 登录 → 工作台空态（未上传库）
    pg.fill("#loginUser", "admin")
    pg.fill("#loginPwd", "admin")
    pg.click(".btn-login")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=os.path.join(OUT, "skin-upload.png"))

    # 3. 直接挂上预置的 demo01 演示库 → 三栏数据态
    pg.evaluate("""() => {
      API_STATE.dbId = 'demo01'; API_STATE.dbName = 'demo01.db';
      API_STATE.online = true; saveApiState();
    }""")
    pg.evaluate("() => renderWorkspace()")
    pg.wait_for_selector("#onlineGrid .t-row", timeout=20000)
    pg.wait_for_timeout(1500)
    # 选中第一个风险孔，让右栏问题列表有内容
    pg.evaluate("""() => {
      const by = (WS.review && WS.review.by_hole) || {};
      const k = Object.keys(by).find(k => by[k].h || by[k].m);
      if (k) wsSelectHole(k);
    }""")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=os.path.join(OUT, "skin-workspace.png"))

    # 4. 深色模式工作台
    pg.evaluate("() => { document.documentElement.setAttribute('data-theme','dark'); }")
    pg.wait_for_timeout(700)
    pg.screenshot(path=os.path.join(OUT, "skin-workspace-dark.png"))

    b.close()
print("done")
