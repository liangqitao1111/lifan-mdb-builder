# -*- coding: utf-8 -*-
"""模块同步脚本：从桌面版复制业务模块到 backend/review/，并做路径适配

用法：python tools/sync_modules.py
- 同步：rule_engine/dao/config/applog/spt_corrector/统计/DXF/toml_preserve
- 适配：base 路径（review → 项目根）与 QColor 惰性保持
"""
import os
import re
import shutil
import sys

DESK = r"C:\Users\神舟\Desktop\lizheng_review_backup_2026.08.11\模块"
WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(WEB, "backend", "review")

MODULES = [
    "rule_engine.py", "dao.py", "config.py", "applog.py", "spt_corrector.py",
    "bearing_capacity.py", "soil_stats.py", "soil_stats_v2.py",
    "karst_report.py", "karst_report_a.py", "issue_exporter.py",
    "profile_dxf.py", "column_dxf.py", "profile_strip.py",
    "toml_preserve.py", "sqlite_dao.py",
]

BASE_OLD = "os.path.dirname(os.path.dirname(__file__))"
BASE_NEW = "os.path.dirname(os.path.dirname(os.path.dirname(__file__)))"


def main():
    os.makedirs(DEST, exist_ok=True)
    for m in MODULES:
        src = os.path.join(DESK, m)
        if not os.path.exists(src):
            print(f"跳过（桌面版无）: {m}")
            continue
        dst = os.path.join(DEST, m)
        text = open(src, encoding="utf-8").read()
        if m != "sqlite_dao.py":
            text = text.replace(BASE_OLD, BASE_NEW)
        open(dst, "w", encoding="utf-8").write(text)
        print(f"同步: {m}")
    # 参数目录
    p_src = os.path.join(os.path.dirname(DESK), "参数")
    if os.path.exists(p_src):
        p_dst = os.path.join(WEB, "参数")
        os.makedirs(p_dst, exist_ok=True)
        for f in os.listdir(p_src):
            shutil.copy2(os.path.join(p_src, f), os.path.join(p_dst, f))
        print("同步: 参数/")
    print("完成。注意：同步后需重启后端并跑 pytest tests/e2e_api.py 回归。")


if __name__ == "__main__":
    main()
