#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理反 Web · SQLite 工作库
================================================================
网页在线编辑的工作数据库：上传 .mdb 后按实际表结构导入 SQLite，
后续所有增删改查都在 SQLite 上进行（免费 Linux 服务器无压力）。

完成编辑后：导出 work.db + schema.json → 交给 github_trigger.py 生成 .mdb。
================================================================
"""
import json
import os
import sqlite3
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

WORK_DIR = os.path.join(os.path.dirname(__file__), "..", "work")
os.makedirs(WORK_DIR, exist_ok=True)


def conn_for(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def sqlite_type_of(value):
    """由 Python 值推断 SQLite 列类型（TEXT/INTEGER/REAL）"""
    if value is None:
        return "TEXT"
    if isinstance(value, bool):
        return "INTEGER"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def import_mdb(db_path, mdb_path, limit_per_table=None):
    """
    把 .mdb 所有表导入 SQLite 工作库。
    返回 {表名: 导入行数}
    """
    from mdb_reader import MdbReader

    reader = MdbReader()
    data = reader.read_all(mdb_path, limit_per_table)
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = conn_for(db_path)
    stats = {}
    try:
        for tname, payload in data.items():
            cols = payload["columns"]
            rows = payload["rows"]
            if not cols:
                continue
            # 列类型：取首行非空值推断；行数>0 用整列推断更稳
            col_types = {}
            for ci, c in enumerate(cols):
                col_types[c] = "TEXT"
                for row in rows:
                    if ci < len(row) and row[ci] is not None and str(row[ci]) != "":
                        col_types[c] = sqlite_type_of(row[ci])
                        break
            col_defs = ", ".join(f'"{c}" {col_types[c]}' for c in cols)
            # 无 id 列时补自增主键（便于行级更新/删除）
            pk = ""
            if "id" not in cols:
                pk = ', "id" INTEGER PRIMARY KEY AUTOINCREMENT'
            conn.execute(f'CREATE TABLE "{tname}" ({col_defs}{pk})')
            placeholders = ", ".join("?" for _ in cols)
            quoted_cols = ", ".join('"' + c + '"' for c in cols)
            insert_sql = f'INSERT INTO "{tname}" ({quoted_cols}) VALUES ({placeholders})'
            n = 0
            for row in rows:
                # 补齐/截断列宽
                vals = list(row[:len(cols)]) + [None] * (len(cols) - len(row))
                # None -> None 保持；空串保留
                try:
                    conn.execute(insert_sql, vals)
                    n += 1
                except Exception as e:
                    print(f"[db] 跳过行 {n} ({tname}): {e}")
            stats[tname] = n
        conn.commit()
    finally:
        conn.close()
    print(f"[db] 导入完成: {stats}")
    return stats


def export_schema(db_path):
    """从 SQLite 工作库导出 schema.json（供 github_trigger / sqlite_to_mdb 使用）"""
    conn = conn_for(db_path)
    tables = {}
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in names:
            cols = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
            tables[t] = {
                "columns": [{"name": c[1], "type": c[2]} for c in cols],
                "primary_key": next((c[1] for c in cols if c[5] > 0), None),
            }
    finally:
        conn.close()
    return tables


def list_tables(db_path):
    conn = conn_for(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    finally:
        conn.close()


def get_rows(db_path, table, page=1, page_size=50, keyword=None, search_col=None):
    """分页读表；keyword 匹配 search_col（或所有文本列）"""
    conn = conn_for(db_path)
    try:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        where, params = "", []
        if keyword:
            targets = [search_col] if search_col else [c for c in cols]
            where = " WHERE " + " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in targets)
            params = [f"%{keyword}%"] * len(targets)
        offset = (page - 1) * page_size
        rows = conn.execute(
            f'SELECT * FROM "{table}" {where} LIMIT ? OFFSET ?', params + [page_size, offset]
        ).fetchall()
        return {"columns": cols, "rows": [dict(r) for r in rows], "total": total,
                "page": page, "page_size": page_size}
    finally:
        conn.close()


def upsert_row(db_path, table, data, row_id=None):
    """新增或更新一行；data: {列: 值}"""
    conn = conn_for(db_path)
    try:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        data = {k: v for k, v in data.items() if k in cols}
        if row_id is not None and "id" in cols:
            sets = ", ".join(f'"{k}" = ?' for k in data)
            conn.execute(f'UPDATE "{table}" SET {sets} WHERE id = ?', list(data.values()) + [row_id])
        else:
            keys = list(data.keys())
            placeholders = ", ".join("?" for _ in keys)
            quoted = ", ".join('"' + k + '"' for k in keys)
            cur = conn.execute(
                f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
                list(data.values()))
            row_id = cur.lastrowid
        conn.commit()
        return {"ok": True, "row_id": row_id}
    finally:
        conn.close()


def delete_row(db_path, table, row_id):
    conn = conn_for(db_path)
    try:
        conn.execute(f'DELETE FROM "{table}" WHERE id = ?', [row_id])
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


if __name__ == "__main__":
    # 自检：用 GitHub Actions 生成的示例 .lz 导入并导出 schema
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "lifan_work_selfcheck.db")
    src = os.path.join(os.path.dirname(__file__), "..", "sample", "lizheng_review_sample.lz")
    stats = import_mdb(tmp, src)
    print("导入统计:", stats)
    sch = export_schema(tmp)
    print("schema 表数:", len(sch))
    for t, s in list(sch.items())[:3]:
        print(f"  [{t}] 列: {[c['name'] for c in s['columns']]}")
    os.remove(tmp)
    print("✓ 自检通过")
