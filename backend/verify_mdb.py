# -*- coding: utf-8 -*-
"""
verify_mdb.py —— 回读校验：生成的 .lz/.mdb 与 SQLite 源必须一致
  · 每表行数逐一比对
  · 每表抽样前 50 行、全列值级比对（宽松类型：数值/日期/字节归一化）
  · SQLite 侧排除 schema 标记 injected_id 的列（与重建跳过逻辑对应）
  · --mdb 支持 .lz（ZIP）——自动解压取内嵌 LZGICAD1.mdb（排除含“备份”的条目）
任何不一致退出码 1，CI 标红，防止“静默丢数”。

用法：python verify_mdb.py --mdb out.lz --sqlite work.db [--schema schema.json]
      --schema 缺省时自动在 sqlite 同目录找 schema.json / {stem}_schema.json
"""
import argparse
import datetime
import decimal
import os
import sqlite3
import sys
import tempfile
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ACE_DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"
SAMPLE_SIZE = 50


# ---------------------------------------------------------------- 连接
def open_mdb_conn(path):
    """优先 pyodbc，回退 pypyodbc（两者读 ACE .mdb 均可）"""
    last_err = None
    for mod_name in ("pyodbc", "pypyodbc"):
        try:
            mod = __import__(mod_name)
            conn = mod.connect(rf"DRIVER={ACE_DRIVER};DBQ={path};")
            return mod_name, conn
        except Exception as e:                       # noqa: BLE001
            last_err = e
    sys.exit(f"无法用 pyodbc/pypyodbc 打开 {path}：{last_err}")


def extract_mdb(lz_path):
    """.lz（ZIP）→ 解压出内嵌 .mdb 到临时目录，返回 (mdb_path, tmpdir)"""
    with zipfile.ZipFile(lz_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith((".mdb", ".accdb"))]
        names = [n for n in names if "备份" not in n]
        if not names:
            sys.exit(f"✗ {lz_path} 内没有 .mdb 条目")
        target = next((n for n in names if "lzgicad1" in n.lower()), names[0])
        tmpdir = tempfile.mkdtemp(prefix="lifan_verify_")
        z.extract(target, tmpdir)
    return os.path.join(tmpdir, target), tmpdir


# ---------------------------------------------------------------- schema
def find_schema(sqlite_path, explicit=None):
    if explicit:
        if not os.path.exists(explicit):
            sys.exit(f"✗ schema 文件不存在: {explicit}")
        return explicit
    d = os.path.dirname(os.path.abspath(sqlite_path))
    stem = os.path.splitext(os.path.basename(sqlite_path))[0]
    for cand in (os.path.join(d, "schema.json"),
                 os.path.join(d, f"{stem}_schema.json"),
                 os.path.join(d, f"{stem}.schema.json")):
        if os.path.exists(cand):
            return cand
    return None


def load_schema_meta(sqlite_path, explicit=None):
    """返回 {表名: {"exclude": set(列), "pk": str|None, "mdb_types": {列: mdb_type}}}"""
    path = find_schema(sqlite_path, explicit)
    meta = {}
    if not path:
        print("ℹ 未找到 schema.json，跳过 injected_id 排除（按全列比对）")
        return meta
    with open(path, "r", encoding="utf-8") as f:
        schema = json_load(f)
    for tname, tdef in (schema.get("tables") or {}).items():
        exclude, pk_cols, mdb_types = set(), [], {}
        cols = tdef.get("columns")
        if isinstance(cols, dict):
            cols = [dict(v, name=k) for k, v in cols.items()]
        for c in cols or []:
            if c.get("injected_id"):
                exclude.add(c["name"])
            if c.get("primary_key") and c["name"] not in pk_cols:
                pk_cols.append(c["name"])
            if c.get("mdb_type"):
                mdb_types[c["name"]] = str(c["mdb_type"]).upper()
        if not pk_cols and tdef.get("primary_key"):
            pk_cols = [tdef["primary_key"]]
        meta[tname] = {"exclude": exclude, "pk": (pk_cols[0] if pk_cols else None),
                       "pk_cols": pk_cols, "mdb_types": mdb_types}
    return meta


def json_load(f):
    import json
    return json.load(f)


# ---------------------------------------------------------------- 比对
def loose_eq(a, b):
    """宽松类型相等：None/数值(含 Decimal/bool)/日期/字节/字符串归一化后比较"""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, (bytes, bytearray)) or isinstance(b, (bytes, bytearray)):
        return bytes(a) == bytes(b)
    if isinstance(a, bool) and not isinstance(b, bool):
        a = 1 if a else 0
    if isinstance(b, bool) and not isinstance(a, bool):
        b = 1 if b else 0
    if isinstance(a, (int, float, decimal.Decimal)) and isinstance(b, (int, float, decimal.Decimal)):
        try:
            return abs(float(a) - float(b)) < 1e-6 * max(1.0, abs(float(a)), abs(float(b)))
        except (TypeError, ValueError):
            pass
    if isinstance(a, (datetime.datetime, datetime.date)) or isinstance(b, (datetime.datetime, datetime.date)):
        da = _as_dt(a)
        db = _as_dt(b)
        if da is not None and db is not None:
            return da == db
    return str(a).strip() == str(b).strip()


def _as_dt(v):
    if isinstance(v, datetime.datetime):
        return v
    if isinstance(v, datetime.date):
        return datetime.datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        try:
            return datetime.datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


# Access 无法对 MEMO / LONGBINARY（OLE 对象）排序 → 只按可排序列排序
UNORDERABLE_TYPES = ("MEMO", "LONGBINARY", "OLE", "BLOB")


def _sortable(cmp_cols, mdb_types, sqlite_types):
    """mdb_types: {列: mdb_type}（来自 schema v2）；sqlite_types: {列: sqlite 声明类型}"""
    out = []
    for c in cmp_cols:
        mt = (mdb_types.get(c) or "").upper()
        st = (sqlite_types.get(c) or "").upper()
        if any(u in mt for u in UNORDERABLE_TYPES) or "BLOB" in st:
            continue
        out.append(c)
    return out


def fetch_rows_sqlite(conn, table, cmp_cols):
    """全量读取可比较列（值集合比对用）"""
    cols_sql = ", ".join(f'"{c}"' for c in cmp_cols) or "rowid"
    return [tuple(r) for r in conn.execute(f'SELECT {cols_sql} FROM "{table}"')]


def fetch_rows_mdb(conn, table, cmp_cols):
    """全量读取可比较列（无 ORDER BY）"""
    cols_sql = ", ".join(f"[{c}]" for c in cmp_cols) or "1"
    cur = conn.cursor()
    cur.execute(f"SELECT {cols_sql} FROM [{table}]")
    return [tuple(r) for r in cur.fetchall()]


def norm_row(row):
    """值归一化：None→空串；数值→float；bytes 原样；日期→datetime；其余 str 去首尾空白。
    使两侧（SQLite/ACE）类型差异在比较前对齐"""
    out = []
    for v in row:
        if v is None:
            out.append("")
        elif isinstance(v, (bytes, bytearray)):
            out.append(bytes(v))
        elif isinstance(v, (int, float, decimal.Decimal)):
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                out.append(str(v).strip())
        elif isinstance(v, (datetime.datetime, datetime.date)):
            out.append(_as_dt(v))
        else:
            s = str(v).strip()
            # 日期字符串归一化：'2024-02-03' ↔ datetime(2024,2,3)（ACE 返回 datetime 对象）
            dt = _as_dt(s)
            out.append(dt if dt is not None else s)
    return tuple(out)


def compare_multiset(srows, mrows, cmp_cols):
    """多集比对：两侧各自归一化 → Python 统一排序 → 逐条比较。
    天然无序（不受数据库排序规则影响）、支持重复行；返回差异行列表"""
    s = sorted(norm_row(r) for r in srows)
    m = sorted(norm_row(r) for r in mrows)
    bad = []
    if len(s) != len(m):
        return [f"行数不一致（值集层面）: sqlite={len(s)} vs mdb={len(m)}"]
    for i, (sr, mr) in enumerate(zip(s, m)):
        if sr != mr:
            for ci in range(min(len(sr), len(mr))):
                if sr[ci] != mr[ci]:
                    bad.append(f"第{i+1}行(排序后) 列[{cmp_cols[ci]}]: sqlite={sr[ci]!r} vs mdb={mr[ci]!r}")
                    if len(bad) >= 30:
                        return bad
    return bad


def main():
    ap = argparse.ArgumentParser(description="回读校验 .lz/.mdb 与 SQLite 一致性")
    ap.add_argument("--mdb", required=True, help="生成的 .lz（ZIP）或 .mdb 路径")
    ap.add_argument("--sqlite", required=True, help="SQLite 工作库路径")
    ap.add_argument("--schema", default=None, help="schema.json（v2），缺省自动查找")
    args = ap.parse_args()

    meta = load_schema_meta(args.sqlite, args.schema)

    # 解包（.lz → 内嵌 mdb）
    src = args.mdb
    tmpdir = None
    if src.lower().endswith((".lz", ".zip")):
        src, tmpdir = extract_mdb(args.mdb)
        print(f"ℹ 已解压内嵌 MDB: {src}")

    mod_name, mconn = open_mdb_conn(src)
    print(f"ℹ MDB 后端: {mod_name}")

    # 表清单
    conn = sqlite3.connect(args.sqlite)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]

    bad = []
    total_rows = 0
    checked = 0
    try:
        mcur = mconn.cursor()
        for t in tables:
            # 行数
            (n,) = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()
            try:
                mcur.execute(f"SELECT COUNT(*) FROM [{t}]")
                (m,) = mcur.fetchone()
            except Exception as e:                     # noqa: BLE001
                m = None
            if m is None:
                bad.append(f"{t}: sqlite={n} 行 vs mdb=缺表")
                continue
            if m != n:
                bad.append(f"{t}: sqlite={n} vs mdb={m} 行不一致")
                continue
            total_rows += n

            # 全量多集比对（排除 injected_id；无排序假设——Access 返回顺序不受控，
            # SQLite/ACE 中文排序规则不同，任何 ORDER BY 对齐都可能错位误报）
            exclude = meta.get(t, {}).get("exclude", set())
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
            cmp_cols = [c for c in cols if c not in exclude]
            if not cmp_cols:
                continue
            srows = fetch_rows_sqlite(conn, t, cmp_cols)
            mrows = fetch_rows_mdb(mconn, t, cmp_cols)
            diffs = compare_multiset(srows, mrows, cmp_cols)
            if diffs:
                for d in diffs[:30]:
                    bad.append(f"{t}: {d}")
            else:
                checked += 1
    finally:
        conn.close()
        mconn.close()
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    if bad:
        print("✗ 校验失败：")
        for line in bad[:30]:
            print(f"  {line}")
        if len(bad) > 30:
            print(f"  …（共 {len(bad)} 处不一致）")
        sys.exit(1)
    print(f"✓ 校验通过：{len(tables)} 张表行数一致（共 {total_rows} 行），"
          f"{checked} 张表全量多集值比对一致（无排序假设）")


if __name__ == "__main__":
    main()
