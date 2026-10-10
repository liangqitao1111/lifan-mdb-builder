# -*- coding: utf-8 -*-
"""造一个带理正规范表结构 + 真实复核问题的演示工作库（用于换皮效果图）。
直接在后端 work/dbs 下生成 demo01.db，并写一份最小 schema.json。"""
import os, sys, json, sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))
sys.path.insert(0, os.path.join(BASE, "backend", "review"))

DB_DIR = os.path.join(BASE, "work", "dbs")
os.makedirs(DB_DIR, exist_ok=True)
DB = os.path.join(DB_DIR, "demo01.db")
if os.path.exists(DB):
    os.remove(DB)

con = sqlite3.connect(DB)
c = con.cursor()

# 钻孔表
c.execute('CREATE TABLE z_ZuanKong (ZKBH TEXT, ZKX REAL, ZKY REAL, ZKSD REAL, ZKBG REAL, ZKLC REAL, ZKPIL REAL, id INTEGER PRIMARY KEY AUTOINCREMENT)')
# 地层表
c.execute('''CREATE TABLE z_g_TuCeng (
  GCSY TEXT, ZKBH TEXT, TCXH INTEGER, TCZCBH INTEGER, TCYCBH INTEGER,
  TCDZSD TEXT, TCDZCY TEXT, TCCDSD REAL, TCHD REAL, TCYMC TEXT, TCMC TEXT,
  TCYS TEXT, TCMSD TEXT, TCSID TEXT, TCKSX TEXT, TCFHCD TEXT, TCMS TEXT,
  id INTEGER PRIMARY KEY AUTOINCREMENT)''')
# 标贯
c.execute('CREATE TABLE z_y_BiaoGuan (ZKBH TEXT, BGDSD REAL, BGGC REAL, BGJS REAL, BGXZJS REAL, id INTEGER PRIMARY KEY AUTOINCREMENT)')
# 动探
c.execute('CREATE TABLE z_y_DongTan (ZKBH TEXT, DTDSD REAL, DTLX TEXT, DTJS REAL, DTXZJS REAL, id INTEGER PRIMARY KEY AUTOINCREMENT)')
# 水位
c.execute('CREATE TABLE z_g_ShuiWei (ZKBH TEXT, SWCH INTEGER, SWSD REAL, SWLX TEXT, SWXZ TEXT, CY INTEGER, SWCSRQ TEXT, id INTEGER PRIMARY KEY AUTOINCREMENT)')
# 取样（土工）
c.execute('''CREATE TABLE z_c_QuYang (ZKBH TEXT, QYBH TEXT, QYSD REAL, QYHSL REAL, QYYX REAL, QYSY REAL,
  QYZXMD REAL, QYZDMD REAL, QYSTXS REAL, QYDC TEXT, QYLX TEXT, QYZLMD REAL,
  id INTEGER PRIMARY KEY AUTOINCREMENT)''')
# 颗分
c.execute('''CREATE TABLE z_c_KeFen (ZKBH TEXT, QYBH TEXT, KLSYFF TEXT, KL800 REAL, KL400 REAL, KL200 REAL,
  KL60 REAL, KL40 REAL, KL20 REAL, KL10 REAL, KL5 REAL, KL2 REAL, KL1 REAL,
  KL_5 REAL, KL_25 REAL, KL_075 REAL, KL0 REAL, id INTEGER PRIMARY KEY AUTOINCREMENT)''')

holes = []
for i in range(1, 13):
    zk = f"ZK-{i:03d}"
    holes.append((zk, 100.0 + i * 20, 200.0 + i * 15, 20.0 + (i % 4), 18.5 + (i % 3), 0, 0))
c.executemany('INSERT INTO z_ZuanKong (ZKBH,ZKX,ZKY,ZKSD,ZKBG,ZKLC,ZKPIL) VALUES (?,?,?,?,?,?,?)', holes)

# 地层：每孔 5-7 层，故意制造软塑/超厚/定名不一致等问题
strata_templates = [
    ("①", 1, 0, "Q4ml", "人工填土", 1.8, 1.8, "杂填土", "杂填土", "杂色", "松散", "稍湿", "", "", "杂色，松散，含建筑垃圾约15%"),
    ("②", 2, 0, "Q4al", "冲积", 4.6, 2.8, "粉质黏土", "粉质黏土", "黄褐", "", "湿", "软塑", "", "黄褐色，含少量铁锰质结核"),
    ("③", 3, 0, "Q4al", "冲积", 7.9, 3.3, "粉土", "粉土", "灰黄", "稍密", "很湿", "", "", "摇振反应中等，无光泽反应"),
    ("④", 4, 0, "Q3al", "冲积", 12.4, 4.5, "细砂", "细砂", "灰白", "中密", "饱和", "", "", "矿物成分以石英、长石为主"),
    ("⑤", 5, 0, "Q3al", "冲积", 16.2, 3.8, "粉质黏土", "粉质黏土", "褐黄", "", "湿", "硬塑", "", "切面稍光滑，干强度中等"),
    ("⑥", 6, 0, "K2", "沉积", 19.5, 3.3, "泥岩", "强风化泥岩", "紫红", "", "", "", "强风化", "岩芯破碎，呈碎块状"),
    ("⑦", 7, 0, "K2", "沉积", 25.0, 5.5, "泥岩", "中风化泥岩", "紫红", "", "", "", "中风化", "岩芯呈短柱状，RQD≈62%"),
]
rows = []
for i in range(1, 13):
    zk = f"ZK-{i:03d}"
    n = 5 + (i % 3)  # 5..7 层
    for t in strata_templates[:n]:
        (tcxh, zc, yc, dzsd, dzcy, ccdsd, chd, ymc, mc, ys, msd, sid, ksx, fhcd, ms) = t
        # 深度逐孔微调
        ccdsd = round(ccdsd + (i % 3) * 0.2, 2)
        # 制造问题：砂土层(④)漏密实度 → R-DEN-005 / 漏湿度 → R-DEN-009
        if ymc == "细砂":
            if i in (2, 5, 8, 11):
                msd = ""          # R-DEN-005 砂土缺少密实度
                ms = "矿物成分以石英、长石为主"  # 描述里也无密实度关键词
            if i in (3, 6, 9):
                sid = ""          # R-DEN-009 砂土缺少湿度描述
                ms = "矿物成分以石英、长石为主"
        # 制造 R-CRS-001：把②层粉质黏土换成淤泥并标注密实度（软土不应标密实度）
        if i in (7, 10) and ymc == "粉质黏土" and zc == 2:
            ymc = mc = "淤泥质土"
            msd, ksx = "软", ""   # 软土标密实度 → R-CRS-001
            ms = "灰黑色，流塑，含腐殖质"
        # 制造 M 级 R-DEN-010：在④细砂(中密)之下⑥位置插一层"松散"砂（深部比上部更松）
        if i in (1, 4, 8) and ymc == "泥岩" and zc == 6:
            ymc = mc = "中砂"
            msd, sid, ksx, fhcd = "松散", "饱和", "", ""
            ys = "灰黄"
            ms = "含少量云母碎片"
        rows.append(("G2024", zk, zc, zc, yc, dzsd, dzcy, ccdsd, chd, ymc, mc, ys, msd, sid, ksx, fhcd, ms))
c.executemany('''INSERT INTO z_g_TuCeng (GCSY,ZKBH,TCXH,TCZCBH,TCYCBH,TCDZSD,TCDZCY,TCCDSD,TCHD,TCYMC,TCMC,TCYS,TCMSD,TCSID,TCKSX,TCFHCD,TCMS)
  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)

# 标贯 / 动探 / 水位 / 取样，给部分孔加数据
bg = []
for i in range(1, 13):
    zk = f"ZK-{i:03d}"
    for d in (2.0, 4.0, 6.0, 8.0):
        bg.append((zk, d, d + 3.0, 5.0 + (i % 5), 6.0 + (i % 5)))
c.executemany('INSERT INTO z_y_BiaoGuan (ZKBH,BGDSD,BGGC,BGJS,BGXZJS) VALUES (?,?,?,?,?)', bg)

dt = []
for i in range(1, 13):
    zk = f"ZK-{i:03d}"
    for d in (3.0, 6.0, 9.0):
        dt.append((zk, d, "重型" if i % 2 else "轻型", 7.0 + (i % 4), 8.0 + (i % 4)))
c.executemany('INSERT INTO z_y_DongTan (ZKBH,DTDSD,DTLX,DTJS,DTXZJS) VALUES (?,?,?,?,?)', dt)

sw = [(f"ZK-{i:03d}", 1, round(2.5 + (i % 3) * 0.3, 2), "潜水", "孔隙水", 1, "2024-03-15") for i in range(1, 13)]
c.executemany('INSERT INTO z_g_ShuiWei (ZKBH,SWCH,SWSD,SWLX,SWXZ,CY,SWCSRQ) VALUES (?,?,?,?,?,?,?)', sw)

qy = []
for i in range(1, 13):
    zk = f"ZK-{i:03d}"
    qy.append((zk, f"{zk}-T1", 4.0, 24.5 + (i % 6), 32.0 + (i % 5), 18.0 + (i % 4), 1.55, 1.72, 0.85, "粉质黏土", "原状", 19.2))
c.executemany('INSERT INTO z_c_QuYang (ZKBH,QYBH,QYSD,QYHSL,QYYX,QYSY,QYZXMD,QYZDMD,QYSTXS,QYDC,QYLX,QYZLMD) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', qy)

con.commit()
con.close()

# 最小 schema.json（build/列宽用不到复核，但保持目录完整）
schema = {"version": 2, "db_id": "demo01", "tool": "理反 Web",
          "tables": {t: {"comment": "", "columns": []} for t in
                     ["z_ZuanKong", "z_g_TuCeng", "z_y_BiaoGuan", "z_y_DongTan", "z_g_ShuiWei", "z_c_QuYang", "z_c_KeFen"]}}
with open(os.path.join(DB_DIR, "demo01_schema.json"), "w", encoding="utf-8") as f:
    json.dump(schema, f, ensure_ascii=False, indent=2)

print("created", DB)
