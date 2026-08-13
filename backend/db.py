#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理反 Web · SQLite 工作库
================================================================
网页在线编辑的工作数据库：上传 .mdb 后按实际表结构导入 SQLite，
后续所有增删改查都在 SQLite 上进行（免费 Linux 服务器无压力）。

完成编辑后：导出 work.db + schema.json（v2，契约 §3）→ 交给
github_trigger.trigger_build() 生成 .mdb。

类型保真（契约 §3）：
  - 导入时同步记录原始 .mdb 字段类型（mdb_type），禁止从数据值推断；
  - schema.json v2 每列含 name / sqlite_type / mdb_type / primary_key，
    注入列额外带 injected_id:true；
  - schema sidecar 与工作库同名：work/dbs/{db_id}_schema.json。
================================================================
"""
import datetime
import decimal
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

SCHEMA_VERSION = 2


def conn_for(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def schema_path_for(db_path):
    """sidecar 路径：work/dbs/{db_id}_schema.json"""
    return os.path.splitext(db_path)[0] + "_schema.json"


# ---------------------------------------------------------------------------
# mdb_type -> SQLite 类型（由原始字段类型映射，非值推断）
# ---------------------------------------------------------------------------
def sqlite_type_of_mdb(mdb_type):
    t = (mdb_type or "").upper()
    if t in ("LONG", "INTEGER", "SHORT", "BYTE", "COUNTER", "BIT", "BOOL"):
        return "INTEGER"
    if t in ("DOUBLE", "SINGLE", "REAL", "FLOAT"):
        return "REAL"
    if t.startswith("DECIMAL") or t in ("CURRENCY", "NUMERIC"):
        return "NUMERIC"
    if t in ("LONGBINARY", "BINARY", "OLE", "IMAGE"):
        return "BLOB"
    # TEXT(n) / MEMO / DATETIME / GUID 等一律 TEXT（DATETIME 存 ISO 字符串）
    return "TEXT"


def _normalize_value(v):
    """把 ACE 返回的 Python 值转换为可安全写入 SQLite 的标量"""
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, datetime.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, datetime.time):
        return v.strftime("%H:%M:%S")
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return v


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
def import_mdb(db_path, mdb_path, db_id=None):
    """
    把 .mdb 所有表导入 SQLite 工作库，并写出 schema.json v2 sidecar。
    返回 {表名: 导入行数}（无 limit_per_table，全量导入）。
    """
    from mdb_reader import MdbReader

    reader = MdbReader()
    data = reader.read_all(mdb_path)
    db_id = db_id or os.path.splitext(os.path.basename(db_path))[0]
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = conn_for(db_path)
    stats = {}
    schema_meta = {"version": SCHEMA_VERSION, "db_id": db_id, "tables": {}}
    try:
        for tname, payload in data.items():
            cols = payload["columns"]
            rows = payload["rows"]
            mdb_types = payload.get("mdb_types") or {}
            if not cols:
                continue
            # 无 id 列的表注入自增主键（契约 §3 injected_id；大小写不敏感）
            injected = not any(x.lower() == "id" for x in cols)
            col_defs = []
            meta_cols = []
            for c in cols:
                mtype = mdb_types.get(c, "TEXT")
                stype = sqlite_type_of_mdb(mtype)
                col_defs.append(f'"{c}" {stype}')
                meta_cols.append({
                    "name": c,
                    "sqlite_type": stype,
                    "mdb_type": mtype,
                    "primary_key": False,
                })
            pk = ""
            if injected:
                pk = ', "id" INTEGER PRIMARY KEY AUTOINCREMENT'
                meta_cols.append({
                    "name": "id",
                    "sqlite_type": "INTEGER",
                    "mdb_type": "LONG",
                    "primary_key": True,
                    "injected_id": True,
                })
            conn.execute(f'CREATE TABLE "{tname}" ({", ".join(col_defs)}{pk})')
            placeholders = ", ".join("?" for _ in cols)
            quoted_cols = ", ".join('"' + c + '"' for c in cols)
            insert_sql = f'INSERT INTO "{tname}" ({quoted_cols}) VALUES ({placeholders})'
            n = 0
            for row in rows:
                # 补齐/截断列宽 + 类型归一化（datetime/Decimal/bytes）
                vals = [_normalize_value(v) for v in row[:len(cols)]]
                vals += [None] * (len(cols) - len(vals))
                try:
                    conn.execute(insert_sql, vals)
                    n += 1
                except Exception as e:
                    print(f"[db] 跳过行 {n} ({tname}): {e}")
            stats[tname] = n
            schema_meta["tables"][tname] = {"columns": meta_cols}
        conn.commit()
        _write_schema(schema_path_for(db_path), schema_meta)
    finally:
        conn.close()
    print(f"[db] 导入完成: {stats}")
    return stats


# ---------------------------------------------------------------------------
# schema v2 导出/写入
# ---------------------------------------------------------------------------
def _write_schema(path, schema):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)


def export_schema(db_path):
    """
    导出 schema.json v2（契约 §3）：{version, db_id, tables:{表:{columns:[...]}}}。
    优先读导入时生成的 sidecar（含 mdb_type / injected_id）；
    sidecar 缺失（老库）时由 SQLite PRAGMA 兜底重建。
    """
    side = schema_path_for(db_path)
    if os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            return json.load(f)
    # 兜底：老库无 sidecar，从 SQLite 结构重建（mdb_type 按 sqlite_type 映射）
    conn = conn_for(db_path)
    tables = {}
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in names:
            cols = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
            meta = []
            for c in cols:
                meta.append({
                    "name": c[1],
                    "sqlite_type": c[2],
                    "mdb_type": c[2],
                    "primary_key": bool(c[5]),
                })
            tables[t] = {"columns": meta}
    finally:
        conn.close()
    db_id = os.path.splitext(os.path.basename(db_path))[0]
    return {"version": SCHEMA_VERSION, "db_id": db_id, "tables": tables}


# ---------------------------------------------------------------------------
# 查询 / 增删改
# ---------------------------------------------------------------------------
def list_tables(db_path):
    conn = conn_for(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    finally:
        conn.close()


def get_rows(db_path, table, page=1, page_size=50, keyword=None, search_col=None, exact=False,
                 order_by=None, order_dir="asc"):
    """分页读表；keyword 匹配 search_col（或所有文本列）；exact=True 按指定列精确匹配
    （钻孔编号精确过滤，避免 LIKE 前缀相似孔号污染：'26-ZD-GZXT-1' 误匹配 10/11/12…）"""
    conn = conn_for(db_path)
    try:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        # 安全：search_col/order_by 必须命中真实列，否则忽略（防 SQL 注入）
        if search_col and search_col not in cols:
            search_col = ""
        where, params = "", []
        if keyword:
            if exact and search_col:
                where = f' WHERE CAST("{search_col}" AS TEXT) = ?'
                params = [str(keyword)]
            else:
                targets = [search_col] if search_col else [c for c in cols]
                where = " WHERE " + " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in targets)
                params = [f"%{keyword}%"] * len(targets)
        total = conn.execute(f'SELECT COUNT(*) FROM "{table}" {where}', params).fetchone()[0]
        offset = (page - 1) * page_size
        order_sql = ""
        if order_by and order_by in cols:
            order_sql = f' ORDER BY "{order_by}" ' + ("DESC" if str(order_dir).lower() == "desc" else "ASC")
        rows = conn.execute(
            f'SELECT * FROM "{table}" {where}{order_sql} LIMIT ? OFFSET ?', params + [page_size, offset]
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
        if not data:
            raise ValueError(f"无可写字段（表 {table} 列: {cols}）")
        id_col = next((x for x in cols if x.lower() == "id"), None)
        if row_id is not None and id_col:
            sets = ", ".join(f'"{k}" = ?' for k in data)
            conn.execute(f'UPDATE "{table}" SET {sets} WHERE "{id_col}" = ?', list(data.values()) + [row_id])
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
        id_col = "id"
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        for x in cols:
            if x.lower() == "id":
                id_col = x
                break
        conn.execute(f'DELETE FROM "{table}" WHERE "{id_col}" = ?', [row_id])
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


if __name__ == "__main__":
    # 自检：用示例 .lz 导入并导出 schema v2
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "lifan_work_selfcheck.db")
    src = os.path.join(os.path.dirname(__file__), "..", "sample", "lizheng_review_sample.lz")
    stats = import_mdb(tmp, src)
    print("导入统计:", stats)
    sch = export_schema(tmp)
    print("schema 表数:", len(sch["tables"]), "version:", sch.get("version"))
    for t, s in list(sch["tables"].items())[:3]:
        print(f"  [{t}] 列: {[c['name'] for c in s['columns']]}")
    os.remove(tmp)
    print("✓ 自检通过")
