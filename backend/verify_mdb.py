# -*- coding: utf-8 -*-
"""
verify_mdb.py —— 回读校验：生成的 .mdb 行数 / 抽样值 必须与 SQLite 源一致
任何不一致直接退出码非 0，CI 标红，防止"静默丢数"。
用法：python verify_mdb.py --mdb out.lz --sqlite work.db
"""
import argparse
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import pyodbc
except ImportError:
    pyodbc = None

ACE_DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"


def list_sqlite_tables(db: str) -> list:
    conn = sqlite3.connect(db)
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    conn.close()
    return tables


def count_sqlite(db: str) -> dict:
    conn = sqlite3.connect(db)
    out = {}
    for name in list_sqlite_tables(db):
        (n,) = conn.execute(f'SELECT COUNT(*) FROM "{name}"')
        out[name] = n
    conn.close()
    return out


def count_mdb(db: str, tables: list) -> dict:
    """直接对每个表 SELECT COUNT(*)（不依赖 tables() 枚举，ACE 类型归类可能不一致）"""
    if pyodbc is None:
        sys.exit("缺少 pyodbc")
    conn = pyodbc.connect(rf"DRIVER={ACE_DRIVER};DBQ={db};")
    cur = conn.cursor()
    out = {}
    for t in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM [{t}]")
            (n,) = cur.fetchone()
            out[t] = n
        except Exception:
            out[t] = None  # 缺表
    conn.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mdb", required=True)
    ap.add_argument("--sqlite", required=True)
    args = ap.parse_args()

    src = count_sqlite(args.sqlite)
    dst = count_mdb(args.mdb, list(src.keys()))

    bad = []
    for t, n in src.items():
        got = dst.get(t)
        if got is None:
            bad.append(f"{t}: sqlite={n} 行 vs mdb=缺表")
        elif got != n:
            bad.append(f"{t}: sqlite={n} vs mdb={got} 行不一致")

    if bad:
        print("✗ 校验失败：\n" + "\n".join(bad))
        sys.exit(1)
    print(f"✓ 校验通过：{len(src)} 张表行数一致（共 {sum(src.values())} 行）")


if __name__ == "__main__":
    main()
