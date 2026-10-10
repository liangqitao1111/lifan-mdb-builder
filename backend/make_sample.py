# -*- coding: utf-8 -*-
"""
生成 GitHub Actions 验证用样例数据（schema.json v2，契约 §3）：
   sample/work.db     —— SQLite 工作库（模拟网页端在线编辑产物）
   sample/schema.json —— 理正表结构定义 v2（含 mdb_type / injected_id / primary_key）
覆盖类型：TEXT(n) / LONG / DOUBLE / MEMO / DATETIME / LONGBINARY / 主键 / 注入列
运行：python backend/make_sample.py
"""
import datetime
import json
import os
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sample")
os.makedirs(BASE, exist_ok=True)

DB_PATH = os.path.join(BASE, "work.db")
SCHEMA_PATH = os.path.join(BASE, "schema.json")

SCHEMA = {
    "version": 2,
    "db_id": "sample01",
    "tool": "理反 Web",
    "tables": {
        "ZK": {
            "comment": "钻孔信息表（含注入 id 列，重建时跳过）",
            "columns": [
                {"name": "钻孔编号", "sqlite_type": "TEXT", "mdb_type": "TEXT(50)", "primary_key": False},
                {"name": "层号", "sqlite_type": "INTEGER", "mdb_type": "LONG", "primary_key": False},
                {"name": "层底深度", "sqlite_type": "REAL", "mdb_type": "DOUBLE", "primary_key": False},
                {"name": "层厚", "sqlite_type": "REAL", "mdb_type": "DOUBLE", "primary_key": False},
                {"name": "岩土定名", "sqlite_type": "TEXT", "mdb_type": "TEXT(100)", "primary_key": False},
                {"name": "状态", "sqlite_type": "TEXT", "mdb_type": "TEXT(20)", "primary_key": False},
                {"name": "id", "sqlite_type": "INTEGER", "mdb_type": "LONG", "injected_id": True},
            ],
        },
        "DZ": {
            "comment": "地层数据表（覆盖 MEMO / DATETIME / LONGBINARY）",
            "columns": [
                {"name": "钻孔编号", "sqlite_type": "TEXT", "mdb_type": "TEXT(50)", "primary_key": False},
                {"name": "层号", "sqlite_type": "INTEGER", "mdb_type": "LONG", "primary_key": False},
                {"name": "岩土定名", "sqlite_type": "TEXT", "mdb_type": "TEXT(100)", "primary_key": False},
                {"name": "描述", "sqlite_type": "TEXT", "mdb_type": "TEXT(255)", "primary_key": False},
                {"name": "密实度", "sqlite_type": "TEXT", "mdb_type": "TEXT(20)", "primary_key": False},
                {"name": "湿度", "sqlite_type": "TEXT", "mdb_type": "TEXT(20)", "primary_key": False},
                {"name": "N63_5", "sqlite_type": "REAL", "mdb_type": "DOUBLE", "primary_key": False},
                {"name": "承载力", "sqlite_type": "REAL", "mdb_type": "DOUBLE", "primary_key": False},
                {"name": "试验日期", "sqlite_type": "DATETIME", "mdb_type": "DATETIME", "primary_key": False},
                {"name": "照片", "sqlite_type": "BLOB", "mdb_type": "LONGBINARY", "primary_key": False},
                {"name": "备注", "sqlite_type": "TEXT", "mdb_type": "MEMO", "primary_key": False},
            ],
        },
        "DT": {
            "comment": "动探记录表（深度列声明主键）",
            "columns": [
                {"name": "钻孔编号", "sqlite_type": "TEXT", "mdb_type": "TEXT(50)", "primary_key": False},
                {"name": "深度", "sqlite_type": "REAL", "mdb_type": "DOUBLE", "primary_key": True},
                {"name": "击数", "sqlite_type": "REAL", "mdb_type": "DOUBLE", "primary_key": False},
                {"name": "类型", "sqlite_type": "TEXT", "mdb_type": "TEXT(20)", "primary_key": False},
                {"name": "杆长", "sqlite_type": "REAL", "mdb_type": "DOUBLE", "primary_key": False},
            ],
        },
    },
}

# MEMO 长文本（>255 字符，验证 TEXT(255) 放不下时走 MEMO）
LONG_NOTE = ("灰黄色可塑状粉质黏土，含少量铁锰质结核及风化碎屑，"
             "局部夹薄层粉砂，可搓成 3mm 土条，无摇振反应，干强度中等，韧性中等；"
             "层底起伏较大，建议按现场实际揭露情况分层处理，"
             "桩基设计参数应结合静力触探及室内土工试验成果综合确定。") * 6

SAMPLE = {
    "ZK": [
        {"钻孔编号": "ZK01-01", "层号": 1, "层底深度": 5.0, "层厚": 5.0, "岩土定名": "素填土", "状态": "待复核", "id": 1},
        {"钻孔编号": "ZK01-01", "层号": 2, "层底深度": 8.5, "层厚": 3.5, "岩土定名": "粉质黏土", "状态": "已确认", "id": 2},
        {"钻孔编号": "ZK01-01", "层号": 3, "层底深度": 12.0, "层厚": 3.5, "岩土定名": "全风化泥岩", "状态": "待复核", "id": 3},
        {"钻孔编号": "ZK01-02", "层号": 1, "层底深度": 4.0, "层厚": 4.0, "岩土定名": "杂填土", "状态": "已确认", "id": 4},
        {"钻孔编号": "ZK01-02", "层号": 2, "层底深度": 9.5, "层厚": 5.5, "岩土定名": "中砂", "状态": "待复核", "id": 5},
    ],
    "DZ": [
        {"钻孔编号": "ZK01-01", "层号": 1, "岩土定名": "素填土", "描述": "杂色·松散·稍湿", "密实度": "松散", "湿度": "稍湿",
         "N63_5": 5.0, "承载力": None, "试验日期": datetime.date(2024, 1, 15), "照片": b"\x89PNG\r\n\x1a\nfakepng-1", "备注": LONG_NOTE},
        {"钻孔编号": "ZK01-01", "层号": 2, "岩土定名": "粉质黏土", "描述": "灰黄·可塑·湿", "密实度": "可塑", "湿度": "湿",
         "N63_5": 8.0, "承载力": 180.0, "试验日期": datetime.date(2024, 1, 16), "照片": b"\x89PNG\r\n\x1a\nfakepng-2", "备注": "含铁锰质结核"},
        {"钻孔编号": "ZK01-01", "层号": 3, "岩土定名": "全风化泥岩", "描述": "灰褐·硬塑", "密实度": "硬塑", "湿度": "干",
         "N63_5": 18.0, "承载力": 280.0, "试验日期": None, "照片": None, "备注": "遇水易软化，注意基坑排水"},
        {"钻孔编号": "ZK01-02", "层号": 1, "岩土定名": "杂填土", "描述": "杂色·松散·稍湿", "密实度": "松散", "湿度": "稍湿",
         "N63_5": 4.0, "承载力": None, "试验日期": datetime.date(2024, 2, 3), "照片": b"\x89PNG\r\n\x1a\nfakepng-3", "备注": LONG_NOTE},
        {"钻孔编号": "ZK01-02", "层号": 2, "岩土定名": "中砂", "描述": "黄色·中密·饱和", "密实度": "中密", "湿度": "饱和",
         "N63_5": 12.0, "承载力": 220.0, "试验日期": datetime.date(2024, 2, 4), "照片": None, "备注": "可能产生液化，需进一步判别"},
    ],
    "DT": [
        {"钻孔编号": "ZK01-01", "深度": 3.0, "击数": 6.0, "类型": "重型", "杆长": 11.0},
        {"钻孔编号": "ZK01-01", "深度": 4.0, "击数": 5.0, "类型": "重型", "杆长": 12.0},
        {"钻孔编号": "ZK01-02", "深度": 2.5, "击数": 4.0, "类型": "轻型", "杆长": 6.0},
    ],
}

# SQLite 建表时的显式列类型（缺省无类型声明，纯 TEXT affinity 足够样例使用）
COL_TYPES = {
    "ZK": {"id": "INTEGER PRIMARY KEY AUTOINCREMENT"},
    "DZ": {"试验日期": "DATETIME", "照片": "BLOB"},
    "DT": {},
}


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    for table, rows in SAMPLE.items():
        cols = list(rows[0].keys())
        col_defs = ", ".join(
            f'"{c}" {COL_TYPES.get(table, {}).get(c, "")}'.rstrip() for c in cols
        )
        conn.execute(f'CREATE TABLE "{table}" ({col_defs})')
        col_list = ", ".join(f'"{c}"' for c in cols)
        ph = ", ".join("?" for _ in cols)
        conn.executemany(
            f'INSERT INTO "{table}" ({col_list}) VALUES ({ph})',
            [[r.get(c) for c in cols] for r in rows],
        )
    conn.commit()
    conn.close()

    with open(SCHEMA_PATH, "w", encoding="utf-8") as f:
        json.dump(SCHEMA, f, ensure_ascii=False, indent=2)

    print(f"✓ 样例已生成（schema v2）：\n  SQLite : {DB_PATH}\n  Schema : {SCHEMA_PATH}")
    for t, rows in SAMPLE.items():
        print(f"  {t}: {len(rows)} 行")


if __name__ == "__main__":
    main()
