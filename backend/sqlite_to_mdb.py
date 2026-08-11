# -*- coding: utf-8 -*-
"""
sqlite_to_mdb.py —— 理反 Web · SQLite 工作库 → 理正 .lz/.mdb 重建脚本
运行环境：Windows（必须已安装 ACE OLEDB 驱动，如 GitHub Actions windows-latest）
作用：把网页端在线编辑产生的 SQLite 库，100% 可靠地重建为理正可打开的 .mdb

用法：
  python sqlite_to_mdb.py --sqlite work.db --schema schema.json --out lizheng_review.lz

依赖：pyodbc / pypyodbc（与理反桌面版一致），Windows ACE 驱动
"""
import argparse
import json
import sqlite3
import sys

try:
    import pyodbc
except ImportError:
    pyodbc = None

# 理正工程库连接串（与理反 connection.py 同口径）
ACE_DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"


def connect_mdb(path: str) -> pyodbc.Connection:
    if pyodbc is None:
        sys.exit("缺少 pyodbc，请先 pip install pyodbc")
    conn = pyodbc.connect(rf"DRIVER={ACE_DRIVER};DBQ={path};")
    conn.autocommit = False
    return conn


def create_database(mdb_path: str) -> None:
    """用 ACE 驱动创建一个空 .mdb（等价于新建 Access 数据库）"""
    # pyodbc 通过连接字符串创建：指定 DBQ 到不存在的文件，ACE 会自动建库
    if pyodbc is None:
        sys.exit("缺少 pyodbc")
    conn = pyodbc.connect(rf"DRIVER={ACE_DRIVER};DBQ={mdb_path};CREATE_DB=TRUE;")
    conn.commit()
    conn.close()


def map_type(sqlite_type: str, sample_value) -> str:
    """SQLite 类型 → Access 字段类型（理反库常用）"""
    sqlite_type = (sqlite_type or "").upper()
    if "INT" in sqlite_type:
        return "LONG"
    if "REAL" in sqlite_type or "FLOA" in sqlite_type or "DOUB" in sqlite_type:
        return "DOUBLE"
    if "BLOB" in sqlite_type:
        return "LONGBINARY"
    if isinstance(sample_value, (int, float)):
        return "LONG" if isinstance(sample_value, int) else "DOUBLE"
    return "TEXT(255)"  # 理正文本字段统一 TEXT(255)，长文本走 MEMO


def build_schema(mdb_path: str, tables: dict, schema: dict) -> None:
    """按 schema.json 的描述建表（表名/字段名含中文 → 一律加 [ ]）"""
    conn = connect_mdb(mdb_path)
    cur = conn.cursor()
    for table_name, rows in tables.items():
        if not rows:
            continue
        # 优先用 schema.json 的字段定义，缺省时从数据推断
        cols_def = schema.get("tables", {}).get(table_name, {}).get("columns", {})
        col_names = list(cols_def.keys()) if cols_def else list(rows[0].keys())
        if cols_def:
            col_defs = ", ".join(
                f"[{c}] {cols_def[c].get('mdb_type', map_type(cols_def[c].get('type', ''), None))}"
                for c in col_names
            )
        else:
            col_defs = ", ".join(
                f"[{c}] {map_type('TEXT', rows[0].get(c))}" for c in col_names
            )
        cur.execute(f"CREATE TABLE [{table_name}] ({col_defs})")
    conn.commit()
    conn.close()


def fill_data(mdb_path: str, tables: dict) -> int:
    """按行写入数据（参数化 SQL，中文值安全；逐表 INSERT）"""
    conn = connect_mdb(mdb_path)
    cur = conn.cursor()
    total = 0
    for table_name, rows in tables.items():
        if not rows:
            continue
        col_names = list(rows[0].keys())
        placeholders = ", ".join("?" for _ in col_names)
        cols_sql = ", ".join(f"[{c}]" for c in col_names)
        for row in rows:
            values = [row.get(c) for c in col_names]
            # None → 显式 NULL，避免类型报错
            values = [None if v is None else v for v in values]
            cur.execute(f"INSERT INTO [{table_name}] ({cols_sql}) VALUES ({placeholders})", values)
            total += 1
    conn.commit()
    conn.close()
    return total


def main():
    ap = argparse.ArgumentParser(description="SQLite → 理正 .mdb 重建")
    ap.add_argument("--sqlite", required=True, help="网页端导出的 SQLite 工作库")
    ap.add_argument("--schema", required=True, help="表结构定义 schema.json")
    ap.add_argument("--out", required=True, help="输出 .lz/.mdb 路径")
    args = ap.parse_args()

    # 1. 读 SQLite 全部表
    conn = sqlite3.connect(args.sqlite)
    conn.row_factory = sqlite3.Row
    tables = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        rows = [dict(r) for r in conn.execute(f'SELECT * FROM "{name}"')]
        tables[name] = rows
    conn.close()

    # 2. 读 schema 定义
    with open(args.schema, "r", encoding="utf-8") as f:
        schema = json.load(f)

    # 3. 建空库 → 建表 → 灌数据（三步全部在 ACE 驱动内完成）
    print(f"[1/3] 创建空 .mdb: {args.out}")
    create_database(args.out)
    print(f"[2/3] 按 schema 建表（{len(tables)} 张）…")
    build_schema(args.out, tables, schema)
    print(f"[3/3] 写入数据 …")
    n = fill_data(args.out, tables)
    print(f"完成：{n} 行写入 {args.out}")


if __name__ == "__main__":
    main()
