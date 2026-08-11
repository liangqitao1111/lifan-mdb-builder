# -*- coding: utf-8 -*-
"""生成 GitHub Actions 验证用样例数据：
   sample/work.db     —— SQLite 工作库（模拟网页端在线编辑产物）
   sample/schema.json —— 理正表结构定义（字段名含中文，与理反真实库同风格）
运行：python backend/make_sample.py
"""
import json
import sqlite3
import os

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sample")
os.makedirs(BASE, exist_ok=True)

DB_PATH = os.path.join(BASE, "work.db")
SCHEMA_PATH = os.path.join(BASE, "schema.json")

SCHEMA = {
    "version": "1.0",
    "tool": "理反 Web",
    "tables": {
        "ZK": {
            "comment": "钻孔信息表",
            "columns": {
                "钻孔编号": {"type": "TEXT", "mdb_type": "TEXT(50)"},
                "层号":     {"type": "INTEGER", "mdb_type": "LONG"},
                "层底深度": {"type": "REAL", "mdb_type": "DOUBLE"},
                "层厚":     {"type": "REAL", "mdb_type": "DOUBLE"},
                "岩土定名": {"type": "TEXT", "mdb_type": "TEXT(100)"},
                "状态":     {"type": "TEXT", "mdb_type": "TEXT(20)"},
            },
        },
        "DZ": {
            "comment": "地层数据表",
            "columns": {
                "钻孔编号": {"type": "TEXT", "mdb_type": "TEXT(50)"},
                "层号":     {"type": "INTEGER", "mdb_type": "LONG"},
                "岩土定名": {"type": "TEXT", "mdb_type": "TEXT(100)"},
                "描述":     {"type": "TEXT", "mdb_type": "TEXT(255)"},
                "密实度":   {"type": "TEXT", "mdb_type": "TEXT(20)"},
                "湿度":     {"type": "TEXT", "mdb_type": "TEXT(20)"},
                "N63_5":    {"type": "REAL", "mdb_type": "DOUBLE"},
                "承载力":   {"type": "REAL", "mdb_type": "DOUBLE"},
            },
        },
        "DT": {
            "comment": "动探记录表",
            "columns": {
                "钻孔编号": {"type": "TEXT", "mdb_type": "TEXT(50)"},
                "深度":     {"type": "REAL", "mdb_type": "DOUBLE"},
                "击数":     {"type": "REAL", "mdb_type": "DOUBLE"},
                "类型":     {"type": "TEXT", "mdb_type": "TEXT(20)"},
                "杆长":     {"type": "REAL", "mdb_type": "DOUBLE"},
            },
        },
    },
}

SAMPLE = {
    "ZK": [
        {"钻孔编号": "ZK01-01", "层号": 1, "层底深度": 5.0, "层厚": 5.0, "岩土定名": "素填土", "状态": "待复核"},
        {"钻孔编号": "ZK01-01", "层号": 2, "层底深度": 8.5, "层厚": 3.5, "岩土定名": "粉质黏土", "状态": "已确认"},
        {"钻孔编号": "ZK01-01", "层号": 3, "层底深度": 12.0, "层厚": 3.5, "岩土定名": "全风化泥岩", "状态": "待复核"},
        {"钻孔编号": "ZK01-02", "层号": 1, "层底深度": 4.0, "层厚": 4.0, "岩土定名": "杂填土", "状态": "已确认"},
        {"钻孔编号": "ZK01-02", "层号": 2, "层底深度": 9.5, "层厚": 5.5, "岩土定名": "中砂", "状态": "待复核"},
    ],
    "DZ": [
        {"钻孔编号": "ZK01-01", "层号": 1, "岩土定名": "素填土", "描述": "杂色·松散·稍湿", "密实度": "松散", "湿度": "稍湿", "N63_5": 5.0, "承载力": None},
        {"钻孔编号": "ZK01-01", "层号": 2, "岩土定名": "粉质黏土", "描述": "灰黄·可塑·湿", "密实度": "可塑", "湿度": "湿", "N63_5": 8.0, "承载力": 180.0},
        {"钻孔编号": "ZK01-01", "层号": 3, "岩土定名": "全风化泥岩", "描述": "灰褐·硬塑", "密实度": "硬塑", "湿度": "干", "N63_5": 18.0, "承载力": 280.0},
        {"钻孔编号": "ZK01-02", "层号": 1, "岩土定名": "杂填土", "描述": "杂色·松散·稍湿", "密实度": "松散", "湿度": "稍湿", "N63_5": 4.0, "承载力": None},
        {"钻孔编号": "ZK01-02", "层号": 2, "岩土定名": "中砂", "描述": "黄色·中密·饱和", "密实度": "中密", "湿度": "饱和", "N63_5": 12.0, "承载力": 220.0},
    ],
    "DT": [
        {"钻孔编号": "ZK01-01", "深度": 3.0, "击数": 6.0, "类型": "重型", "杆长": 11.0},
        {"钻孔编号": "ZK01-01", "深度": 4.0, "击数": 5.0, "类型": "重型", "杆长": 12.0},
        {"钻孔编号": "ZK01-02", "深度": 2.5, "击数": 4.0, "类型": "轻型", "杆长": 6.0},
    ],
}


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    for table, rows in SAMPLE.items():
        cols = list(rows[0].keys())
        col_sql = ", ".join(f'"{c}"' for c in cols)
        conn.execute(f'CREATE TABLE "{table}" ({col_sql})')
        ph = ", ".join("?" for _ in cols)
        conn.executemany(
            f'INSERT INTO "{table}" ({col_sql}) VALUES ({ph})',
            [[r.get(c) for c in cols] for r in rows],
        )
    conn.commit()
    conn.close()

    with open(SCHEMA_PATH, "w", encoding="utf-8") as f:
        json.dump(SCHEMA, f, ensure_ascii=False, indent=2)

    print(f"✓ 样例已生成：\n  SQLite : {DB_PATH}\n  Schema : {SCHEMA_PATH}")
    for t, rows in SAMPLE.items():
        print(f"  {t}: {len(rows)} 行")


if __name__ == "__main__":
    main()
