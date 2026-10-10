# -*- coding: utf-8 -*-
"""
sqlite_to_mdb.py —— 理反 Web · SQLite 工作库 → 理正 .lz（真 ZIP 包）重建脚本
运行环境：Windows（必须已安装 ACE OLEDB 驱动，如 GitHub Actions windows-latest）
作用：把网页端在线编辑产生的 SQLite 库，100% 可靠地重建为理正可打开的 .lz 包

产物结构（模仿理正，契约 §4）：
  {工程名}/database{时间戳}/LZGICAD1.mdb
  {工程名}/ProjectInfo.ini            （GBK 编码）
工程名 = lifan_{db_id}（ASCII）；ZIP 条目名 UTF-8；时间戳 YYYY-MM-DD-HH-MM-SS

用法：
  python sqlite_to_mdb.py --sqlite work.db --schema schema.json --out lizheng_review.lz [--db-id abc]

依赖：pyodbc / pypyodbc（与理反桌面版一致）+ pywin32（ADOX.Catalog 建库）+ Windows ACE 驱动
"""
import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys
import tempfile
import zipfile

# Windows 控制台默认 cp1252，强制 UTF-8 输出（GitHub Actions 环境下必需）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import pyodbc
except ImportError:
    pyodbc = None

# 理正工程库连接串（与理反 connection.py 同口径）
ACE_DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"

# 契约 §4：时间戳格式
TS_FMT = "%Y-%m-%d-%H-%M-%S"


def connect_mdb(path):
    if pyodbc is None:
        sys.exit("缺少 pyodbc，请先 pip install pyodbc")
    conn = pyodbc.connect(rf"DRIVER={ACE_DRIVER};DBQ={path};")
    conn.autocommit = False
    return conn


def create_database(mdb_path):
    """用 ADOX.Catalog 创建空 .mdb（ACE 驱动不支持连接串 CREATE_DB 属性）"""
    if os.path.exists(mdb_path):
        os.remove(mdb_path)
    try:
        import win32com.client
    except ImportError:
        sys.exit("缺少 pywin32，请先 pip install pywin32")
    cat = win32com.client.Dispatch("ADOX.Catalog")
    try:
        cat.Create(f"Provider=Microsoft.ACE.OLEDB.12.0;Data Source={mdb_path}")
    finally:
        cat = None


# ---------------------------------------------------------------- schema v2
def normalize_columns(raw_cols):
    """schema.json v2 的 columns 是 list[dict]，兼容 v1 的 dict{列名:定义}"""
    if raw_cols is None:
        return []
    if isinstance(raw_cols, dict):
        out = []
        for name, meta in raw_cols.items():
            item = dict(meta or {})
            item.setdefault("name", name)
            out.append(item)
        return out
    out = []
    for item in raw_cols:
        if isinstance(item, dict):
            out.append(dict(item))
        elif isinstance(item, str):
            out.append({"name": item})
    return out


def infer_mdb_type(sqlite_type, value):
    """SQLite 类型/样例值 → Access 字段类型（schema 缺 mdb_type 时的兜底）"""
    t = (sqlite_type or "").upper()
    if "INT" in t:
        return "LONG"
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return "DOUBLE"
    if "BLOB" in t:
        return "LONGBINARY"
    if "DATE" in t or "TIME" in t:
        return "DATETIME"
    if isinstance(value, bool):
        return "BIT"
    if isinstance(value, (bytes, bytearray)):
        return "LONGBINARY"
    if isinstance(value, int):
        return "LONG"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, (_dt.datetime, _dt.date)):
        return "DATETIME"
    if isinstance(value, str) and len(value) > 255:
        return "MEMO"
    return "TEXT(255)"


def normalize_mdb_type(mdb_type):
    """把 mdb_type 规范化成 ACE DDL 可识别的写法；未知类型原样透传"""
    t = (mdb_type or "").strip().upper()
    if not t:
        return None
    if t == "TEXT":
        return "TEXT(255)"          # 裸 TEXT → 等长 TEXT(255)
    if t in ("LONG", "DOUBLE", "MEMO", "LONGBINARY", "DATETIME", "BIT",
             "TEXT(1)", "TEXT(2)", "TEXT(4)", "TEXT(8)", "TEXT(16)", "TEXT(20)",
             "TEXT(30)", "TEXT(50)", "TEXT(100)", "TEXT(200)", "TEXT(250)", "TEXT(255)"):
        return t
    if t.startswith("TEXT(") or t.startswith("CHAR(") or t.startswith("VARCHAR("):
        return t
    if t in ("INTEGER", "SINGLE", "BYTE", "CURRENCY", "DECIMAL(10,2)", "GUID"):
        return t
    return t


def prep_value(v):
    """参数化插入前的值清洗：None 保留；bool→int；bytes→bytes；日期原样"""
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (bytearray,)):
        return bytes(v)
    return v


def table_columns(schema_tables, table_name, rows, sqlite_conn=None):
    """
    决定建表列：以 schema 为准（跳过 injected_id 列），
    数据中多余列（schema 未声明）追加在末尾，类型兜底推断。
    sqlite_conn: SQLite 连接（可选）——schema 声明为空且表 0 行时，从 PRAGMA
    table_info 兜底推断列（修复：demo 库 sidecar columns=[] + 0 行表此前整表
    被跳过建表，verify_mdb 报"缺表"使构建 fail-closed 失败）。
    返回 (col_infos, col_names) —— col_infos: [{"name","mdb_type","primary_key"}]
    """
    table_schema = (schema_tables or {}).get(table_name, {})
    raw_cols = table_schema.get("columns")
    cols = normalize_columns(raw_cols)
    table_pk = table_schema.get("primary_key")

    infos = []
    for c in cols:
        if c.get("injected_id"):
            continue                                  # 契约：重建时跳过注入列
        mdb_type = normalize_mdb_type(c.get("mdb_type"))
        if mdb_type is None:
            mdb_type = infer_mdb_type(c.get("sqlite_type") or c.get("type"), None)
        # P2-1：schema 声明 TEXT(n) 但实际数据超长时升级 MEMO——
        # 修复前 ACE 报 "String data, right truncation" 使整个构建中断且无针对性提示
        if mdb_type.startswith("TEXT(") and rows:
            try:
                _n = int(mdb_type[5:-1])
                if _n < 255:
                    _maxlen = max((len(str(row.get(c["name"]) or "")) for row in rows), default=0)
                    if _maxlen > _n:
                        mdb_type = "MEMO"
            except (ValueError, TypeError):
                pass
        pk = bool(c.get("primary_key")) or (table_pk is not None and c.get("name") == table_pk)
        # P2-2：Access 不允许 MEMO/LONGBINARY/OLE 字段作主键/索引——降级为非主键
        # （与 verify_mdb 的 UNORDERABLE_TYPES 同思路），避免 CREATE TABLE 直接抛错
        if pk and mdb_type in ("MEMO", "LONGBINARY", "OLE", "BLOB"):
            pk = False
        infos.append({"name": c["name"], "mdb_type": mdb_type, "primary_key": pk})

    seen = {c["name"] for c in infos}
    if rows:
        for name in rows[0].keys():
            if name not in seen:
                infos.append({
                    "name": name,
                    "mdb_type": infer_mdb_type(None, rows[0].get(name)),
                    "primary_key": False,
                })
                seen.add(name)
    # 兜底：schema 声明为空且 0 行（无样例值可推断）→ 从 SQLite PRAGMA 推断列
    if not infos and sqlite_conn is not None:
        try:
            for c in sqlite_conn.execute(f'PRAGMA table_info("{table_name}")').fetchall():
                name, stype = c[1], c[2]
                if name in seen or (str(name).lower() == "id" and c[5]):
                    # injected 自增主键按契约跳过（schema 无声明时也保持一致）
                    continue
                infos.append({
                    "name": name,
                    "mdb_type": infer_mdb_type(stype, None),
                    "primary_key": bool(c[5]),
                })
                seen.add(name)
        except Exception:
            pass
    return infos


def build_schema(mdb_path, tables, schema, sqlite_conn=None):
    """按 schema.json v2 建表（表名/字段名含中文 → 一律加 [ ]；主键声明 PRIMARY KEY）"""
    conn = connect_mdb(mdb_path)
    cur = conn.cursor()
    schema_tables = (schema or {}).get("tables", {})
    created = 0
    for table_name, rows in tables.items():
        infos = table_columns(schema_tables, table_name, rows, sqlite_conn=sqlite_conn)
        if not infos:
            print(f"  ⚠ 表 [{table_name}] 无有效列（可能全部为 injected_id），跳过")
            continue
        pk_cols = [c["name"] for c in infos if c["primary_key"]]
        parts = []
        for c in infos:
            parts.append(f"[{c['name']}] {c['mdb_type']}")
        if len(pk_cols) == 1:
            # 单列主键：内联 PRIMARY KEY
            for i, c in enumerate(infos):
                if c["name"] == pk_cols[0]:
                    parts[i] = f"[{c['name']}] {c['mdb_type']} PRIMARY KEY"
                    break
        elif len(pk_cols) > 1:
            parts.append("PRIMARY KEY (" + ", ".join(f"[{p}]" for p in pk_cols) + ")")
        ddl = f"CREATE TABLE [{table_name}] ({', '.join(parts)})"
        cur.execute(ddl)
        created += 1
    conn.commit()
    conn.close()
    return created


def fill_data(mdb_path, tables, schema, sqlite_conn=None):
    """按行写入数据（参数化 SQL，中文值安全；跳过 injected_id 列）"""
    conn = connect_mdb(mdb_path)
    cur = conn.cursor()
    total = 0
    schema_tables = (schema or {}).get("tables", {})
    for table_name, rows in tables.items():
        if not rows:
            continue
        infos = table_columns(schema_tables, table_name, rows, sqlite_conn=sqlite_conn)
        if not infos:
            continue
        col_names = [c["name"] for c in infos]
        placeholders = ", ".join("?" for _ in col_names)
        cols_sql = ", ".join(f"[{c}]" for c in col_names)
        for row in rows:
            values = [prep_value(row.get(c)) for c in col_names]
            cur.execute(f"INSERT INTO [{table_name}] ({cols_sql}) VALUES ({placeholders})", values)
            total += 1
    conn.commit()
    conn.close()
    return total


# ---------------------------------------------------------------- .lz 打包
def project_info_ini(project, ts):
    """契约 §4 的 ProjectInfo.ini 模板（GBK 编码写入文件）"""
    return (
        "[GCInfo]\r\n"
        f"GCSY=27\r\n"
        f"GCBH={project}\r\n"
        f"GCMC={project}\r\n"
        f"GCPATH=\\Files{ts}\\{project}\r\n"
        "[DataBase]\r\n"
        "CURMDB=LZGICAD1.mdb\r\n"
        f"CURDB=database{ts}\r\n"
        "[Files]\r\n"
        f"CURFILE=Files{ts}\r\n"
    )


def pack_lz(project, ts, mdb_path, out_path):
    """打真 .lz（ZIP）：{工程名}/database{ts}/LZGICAD1.mdb + {工程名}/ProjectInfo.ini"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(mdb_path, arcname=f"{project}/database{ts}/LZGICAD1.mdb")
        ini = project_info_ini(project, ts)
        zf.writestr(f"{project}/ProjectInfo.ini", ini.encode("gbk"))
    print(f"  ├─ {project}/database{ts}/LZGICAD1.mdb")
    print(f"  └─ {project}/ProjectInfo.ini（GBK）")


def sanitize_db_id(db_id):
    """工程名必须 ASCII：只保留 [A-Za-z0-9_-]，其余替换为 _"""
    out = []
    for ch in db_id:
        out.append(ch if (ch.isascii() and (ch.isalnum() or ch in "_-")) else "_")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(description="SQLite → 理正 .lz（真 ZIP 包）重建")
    ap.add_argument("--sqlite", required=True, help="网页端导出的 SQLite 工作库")
    ap.add_argument("--schema", required=True, help="表结构定义 schema.json（v2）")
    ap.add_argument("--out", required=True, help="输出 .lz 路径")
    ap.add_argument("--db-id", default=None, help="工程标识（ASCII，默认取 sqlite 文件名去扩展名）")
    args = ap.parse_args()

    # 1. 读 SQLite 全部表
    conn = sqlite3.connect(args.sqlite)
    conn.row_factory = sqlite3.Row
    tables = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        rows = [dict(r) for r in conn.execute(f'SELECT * FROM "{name}"')]
        tables[name] = rows

    # 2. 读 schema 定义（v2 权威）
    with open(args.schema, "r", encoding="utf-8") as f:
        schema = json.load(f)

    # 3. 工程名 / 时间戳 / 目录
    if args.db_id:
        db_id = sanitize_db_id(args.db_id)
    else:
        db_id = sanitize_db_id(os.path.splitext(os.path.basename(args.sqlite))[0])
    project = f"lifan_{db_id}"
    ts = _dt.datetime.now().strftime(TS_FMT)
    print(f"工程名: {project}   时间戳: {ts}   表数: {len(tables)}")

    # 4. 建空库 → 建表 → 灌数据（mdb 在临时目录，完成后打包 .lz）
    tmpdir = tempfile.mkdtemp(prefix="lifan_build_")
    mdb_path = os.path.join(tmpdir, project, f"database{ts}", "LZGICAD1.mdb")
    os.makedirs(os.path.dirname(mdb_path), exist_ok=True)
    try:
        print(f"[1/4] 创建空 .mdb（ADOX.Catalog）…")
        create_database(mdb_path)
        print(f"[2/4] 按 schema v2 建表（跳过 injected_id，主键 PRIMARY KEY）…")
        n_tables = build_schema(mdb_path, tables, schema, sqlite_conn=conn)
        print(f"      {n_tables} 张表已创建")
        print(f"[3/4] 写入数据 …")
        n = fill_data(mdb_path, tables, schema, sqlite_conn=conn)
        print(f"      {n} 行已写入")
        print(f"[4/4] 打包 .lz: {args.out}")
        pack_lz(project, ts, mdb_path, args.out)
    finally:
        conn.close()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"完成：{n} 行 / {n_tables} 表 → {args.out}")


if __name__ == "__main__":
    main()
