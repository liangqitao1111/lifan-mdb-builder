"""理反 — 数据访问层 (Access MDB)"""
import os, datetime, traceback, time
from config import DTLX_MAP, SWLX_MAP, SWXZ_MAP, ALLOWED_FIELDS, load_project_config, normalize_state_word, dpt_rod_length_offset


# ---- A类动探杆长修正：工程类型传递（V2.2.2 C4）----
# DAO 本身不知道工程类型；RuleEngine 构造时经 set_project_type 注入模块级
# 工程类型，使既有调用点（get_dpt_data(zkbh) / get_all_dpt() 不传第二参数）
# 在 A 类工程下也能执行杆长修正（修复 P1-10 修复未接线的分支遗漏）。
# 显式传入 project_type 时优先于模块级值。
_DPT_PROJECT_TYPE = None


def set_project_type(project_type):
    """设置模块级工程类型（'A'/'B'/None），供动探杆长修正判断使用"""
    global _DPT_PROJECT_TYPE
    _DPT_PROJECT_TYPE = project_type


# ---- 铁建标贯杆长修正系数 ----
# 来源：项目表 参数\铁建承载力计算表.xlsx → 标贯修正 sheet（TB10012 体系）；
# 知识库无独立条文（常用参数\标贯与触探\标贯杆长修正系数.md 仅含 GB50021 国标
# 与 DBJ15-31 广东两套，无铁建表），故以项目表为权威来源标注（合规审查 Agent3-S2）。
# D1 项目特色：理正原库 BGXZJS 恒 NULL（connection.py 同步强制置空，见其注释），
# 本表仅用于工作库 B类编辑路径重算修正值；与理正原库语义的一致性由 D1 约定保证。
# 本表可从 TOML「公用.标贯杆长修正_B类」配置覆盖。
# V3.0.3（Agent2-4）：24/27/30m 延伸值 0.68/0.65/0.63 的单一来源即本项目表
# （铁建承载力计算表.xlsx 标贯修正 sheet）；知识库《标贯杆长修正系数》仅收录
# 至 21m（3~21m 与项目表逐项吻合），24m+ 段未见铁路规程原文直接佐证、公开
# 文献存在分歧（0.67/0.65/0.63 等），按项目表单一来源执行（合规审查可疑项 2.4）。
_SPT_ROD_LENGTH = [3, 6, 9, 12, 15, 18, 21, 24, 27, 30]
_SPT_COEFF = [1.00, 0.92, 0.86, 0.81, 0.77, 0.73, 0.70, 0.68, 0.65, 0.63]
KEFEN_DEDUPE_0075 = True


def _build_rod_config():
    """重建 dao 模块级派生常量（import 时与配置保存后各调用一次）

    P1-⑤：标贯杆长修正_B类 系数表与 颗分粒组去重开关此前在 import 期固化，
    参数中心修改后不生效；现由 reload_from_config 重建。
    """
    global _SPT_ROD_LENGTH, _SPT_COEFF, KEFEN_DEDUPE_0075
    _cfg = load_project_config()
    _tmp = _cfg.get('公用', {}).get('标贯杆长修正_B类', {})
    if _tmp:
        items = []
        for k, v in _tmp.items():
            if k == '用途': continue
            try:
                length = float(k.replace('m', ''))
                items.append((length, float(v)))
            except (ValueError, TypeError):
                continue
        items.sort()
        # P2-6 同口径：同值双档去重（严格递增，避免插值除零）
        _dedup = []
        for l, c in items:
            if _dedup and _dedup[-1][0] == l:
                _dedup[-1] = (l, c)
            else:
                _dedup.append((l, c))
        items = _dedup
        if len(items) >= 2:
            _SPT_ROD_LENGTH = [l for l, c in items]
            _SPT_COEFF = [c for l, c in items]
    _kefen_cfg = _cfg.get('公用', {}).get('颗分_粒组划分', {})
    KEFEN_DEDUPE_0075 = True
    if isinstance(_kefen_cfg, dict):
        KEFEN_DEDUPE_0075 = bool(_kefen_cfg.get('0.075mm粒组去重', True))


def reload_from_config():
    """配置保存后重建本模块派生常量（review_api._reload_config_modules 调用）"""
    _build_rod_config()


_build_rod_config()


# ---- 颗分粒组 → 统计桶映射（V2.2.3 S2 修正）----
# kf 索引布局与 SELECT 列序一致（0 基）：
#   kf[0:4]  = kl20,kl10,kl5,kl2            → r20_2（>2mm 砾粒）
#   kf[4] kl1(2~1mm)、kf[5] kl_5(1~0.5mm)   → r2_05（2~0.5mm 粗砂）
#   kf[6] kl_25(0.5~0.25mm)                 → r05_025（中砂）
#   kf[7] kl_1(0.25~0.1mm)                  → r025_0075（细砂，0.25~0.075mm）
#   kf[8]/kf[9] kl_075/kl_074（0.1~0.075mm，同一筛孔粒组的不同字段命名，
#              理正8.5/9.0，TOML「0.075mm粒组去重」控制是否只计一次）→ r025_0075
#   kf[10:15] kl_05,kl_01,kl_005,kl_002,kl0 → r0075（<0.075mm 粉黏粒）
# 原实现把 kl_075 归入 r0075 细粒桶（config.py 注释明确 r0075=<0.075mm 含量），
# 且 r2_05/r05_025/r025_0075 桶边界整体错位一档 → 砂粒含量被低估、R-GRS-001
# 定名失真。修正后 kl_075 只归一个粒组（细砂桶），粒组合计恒 100%。
def _kefen_to_buckets(kf):
    """颗分行 → 统计桶（纯函数，供 get_test_data 使用与单测）

    返回 (r20_2, r2_05, r05_025, r025_0075, r0075)；整行为空时对应值 None。
    """
    def _safe_sum(vals):
        non_none = [float(v) for v in vals if v is not None]
        return round(sum(non_none), 1) if non_none else None
    r20_2 = _safe_sum(kf[0:4])
    r2_05 = _safe_sum(kf[4:6])
    r05_025 = round(float(kf[6]), 1) if kf[6] is not None else None
    if KEFEN_DEDUPE_0075:
        d0075 = _max_non_none(kf[8], kf[9])
        r025_0075 = _safe_sum([kf[7], d0075])
    else:
        r025_0075 = _safe_sum(kf[7:10])
    r0075 = _safe_sum(kf[10:15])
    return r20_2, r2_05, r05_025, r025_0075, r0075


def _max_non_none(a, b):
    """两值取大者（None 视为缺失）"""
    if a is None:
        return b
    if b is None:
        return a
    try:
        return max(float(a), float(b))
    except (ValueError, TypeError):
        return a


def _spt_correction_coefficient(bggc):
    """根据杆长(m)线性插值求修正系数a，N_corr = N × a

    系数表来源：项目表 参数\铁建承载力计算表.xlsx 标贯修正 sheet（TB10012 体系，
    3~30m：1.00~0.63）；知识库无独立条文佐证（合规审查 Agent3-S2），
    使用时以项目表口径为准；D1 原库 BGXZJS=NULL 为项目特色约定（connection.py
    同步强制置空），本函数仅服务于工作库 B类编辑路径（update_spt_raw_and_clear_corrected）。
    """
    if bggc <= 0:
        return 1.0
    if bggc <= _SPT_ROD_LENGTH[0]:
        return _SPT_COEFF[0]
    if bggc >= _SPT_ROD_LENGTH[-1]:
        return _SPT_COEFF[-1]
    for i in range(len(_SPT_ROD_LENGTH) - 1):
        lo, hi = _SPT_ROD_LENGTH[i], _SPT_ROD_LENGTH[i + 1]
        if lo < bggc <= hi:
            a_lo, a_hi = _SPT_COEFF[i], _SPT_COEFF[i + 1]
            return a_lo + (a_hi - a_lo) * (bggc - lo) / (hi - lo)
    return 1.0

def _is_heavy_dpt(dtlx_raw):
    """V3.0.2（B4）：动探类型是否重型（N63.5，GB50021 表B.0.1 仅适用重型）

    轻型（N10）/超重型（N120）不参与 N63.5 杆长修正与密实度判别；
    空 DTLX 视为重型（历史数据未录类型，保持旧行为参与判别，避免静默漏报）。
    """
    return dtlx_raw in ('2', '重型', '')


def _log_error(msg):
    """将错误写入日志文件（静默）"""
    try:
        log_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(log_dir, 'lizheng_review.log')
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f'[{ts}] {msg}\n')
    except Exception:
        pass


class DataAccess:
    """理正勘察数据库读写封装"""

    # 标准表名 → 候选表名列表（按优先级排序）
    TABLE_CANDIDATES = {
        'z_ZuanKong': ['z_ZuanKong', 'z_y_BasicInfo', 'z_y_ZuanKong', 'z_BasicInfo', 'ZK_INFO', 'tblBorehole'],
        'z_g_TuCeng': ['z_g_TuCeng', 'z_y_TianYeDiCeng', 'z_y_Strata', 'z_g_Strata', 'z_y_DiCeng', 'tblStrata', 'z_Strata'],
        'z_c_QuYang': ['z_c_QuYang', 'z_y_ShiYan', 'z_y_Test', 'z_c_Test', 'z_ShiYan', 'tblTest'],
        'z_c_KeFen': ['z_c_KeFen', 'z_y_KeFen', 'z_c_Gradation'],
        'z_c_GuJie': ['z_c_GuJie', 'z_y_GuJie', 'z_c_Consolidation'],
        'z_c_ZhiJian': ['z_c_ZhiJian', 'z_y_ZhiJian', 'z_c_Shear'],
        'z_g_ShuiWei': ['z_g_ShuiWei', 'z_y_DiXiaShuiWei', 'z_y_Water', 'z_y_ShuiWei', 'tblWater'],
        'z_y_BiaoGuan': ['z_y_BiaoGuan', 'z_BiaoGuan', 'z_SPT', 'tblSPT'],
        'z_y_DongTan': ['z_y_DongTan', 'z_DongTan', 'z_DPT', 'tblDPT'],
    }

    def __init__(self, conn):
        self.conn = conn
        self._table_map = {}
        self._discover_tables()

    def _discover_tables(self):
        """自动发现数据库中实际存在的表名"""
        cursor = self.conn.cursor()
        for std_name, candidates in self.TABLE_CANDIDATES.items():
            for name in candidates:
                try:
                    cursor.execute(f'SELECT TOP 1 1 FROM [{name}]')
                    self._table_map[std_name] = name
                    break
                except Exception:
                    continue
            if std_name not in self._table_map:
                self._table_map[std_name] = std_name  # 全部失败就用原名

    def _t(self, name):
        """返回标准表名对应的实际表名"""
        return self._table_map.get(name, name)

    def _safe_query(self, query_name, sql, params=None, max_retries=1):
        """带重试的数据库查询，失败后等 0.3s 重试一次"""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                cursor = self.conn.cursor()
                cursor.execute(sql, params or [])
                return cursor
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(0.3)
        _log_error(f'[{query_name}] 读取失败(重试{max_retries}次): {last_error}')
        return None

    @staticmethod
    def _safe_int(v, d=0):
        try:
            return int(v)
        except (ValueError, TypeError):
            return d

    @staticmethod
    def _safe_float(v, d=None):
        if v is None:
            return d
        try:
            return float(v)
        except (ValueError, TypeError):
            return d

    @staticmethod
    def _opt_float(v):
        """转 float，None/非法值返回 None"""
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _calc_il_ip(hsl, yx, sy):
        """液性指数 IL / 塑性指数 IP（单孔/批量三处共用，含除零防护）"""
        il = None
        if hsl is not None and yx is not None and sy is not None and (yx - sy) != 0:
            il = round((hsl - sy) / (yx - sy), 2)
        ip = round(yx - sy, 1) if yx is not None and sy is not None else None
        return il, ip

    @staticmethod
    def _row_to_stratum(r):
        """单条地层行 → dict（get_strata / get_all_strata 共用，P2-4 收敛重复映射）"""
        return {
            'tczcbh': str(r.TCZCBH).strip() if r.TCZCBH else '',
            'tcycbh': str(r.TCYCBH).strip() if r.TCYCBH else '',
            'tcxh': DataAccess._safe_int(r.TCXH, 0),
            'tccdsd': DataAccess._safe_float(r.TCCDSD, 0),
            'tchd': DataAccess._safe_float(r.TCHD, 0),
            'tcymc': (r.TCMC or r.TCYMC or '').strip(),
            'tcmc': r.TCMC.strip() if r.TCMC else '',
            'tcdzsd': str(r.TCDZSD).strip() if r.TCDZSD else '',
            'tcdzcy': str(r.TCDZCY).strip() if r.TCDZCY else '',
            'tcys': r.TCYS.strip() if r.TCYS else '',
            # V2.2.5（S4）：tcksx 带状写法归一化（'硬塑状' → '硬塑'），与 tcmsd 同口径
            # （R-DEN-007/R-PLS 比较均以规范状态词为准，避免带状写法误报）
            'tcksx': normalize_state_word(r.TCKSX.strip() if r.TCKSX else ''),
            # V2.2.3（S7）：tcmsd 带状写法归一化（'稍密状'/'中密状' → '稍密'/'中密'），
            # 避免 R-DEN-001 等规则因 '稍密状' != '稍密' 误报；保留模糊匹配特色
            'tcmsd': normalize_state_word(r.TCMSD.strip() if r.TCMSD else ''),
            'tcsid': r.TCSID.strip() if r.TCSID else '',
            'tcfhcd': r.TCFHCD.strip() if r.TCFHCD else '',
            'tcms': r.TCMS.strip() if r.TCMS else '',
        }

    @staticmethod
    def _row_to_spt(r):
        """单条标贯行 → dict（get_spt_data / get_all_spt 共用）"""
        return {'bgdsd': DataAccess._safe_float(r.BGDSD, 0),
                'bggc': DataAccess._safe_float(r.BGGC, 0),
                'bgjs': DataAccess._safe_int(r.BGJS, 0),
                'bgxzjs': DataAccess._safe_float(r.BGXZJS)}

    def _row_to_dpt(self, r, project_type=None):
        """单条动探行 → dict（get_dpt_data / get_all_dpt 共用；A/B类含杆长修正）"""
        dtdsd = self._safe_float(r.DTDSD, 0)
        dtlx_raw = str(r.DTLX).strip() if r.DTLX else ''
        dtjs = self._safe_int(r.DTJS, 0)
        # V2.2.2（C6）：修正击数按浮点读取（库中可为小数），与 spt_to_weathering
        # 浮点直判同口径；原 _safe_int 截断使 (10,11) 区间修正值密实度判定错位
        dtxzjs = self._safe_float(r.DTXZJS, 0)
        # V3.0.2（B4）：仅重型动探（DTLX='2'/中文'重型'）参与 N63.5 杆长修正；
        # 轻型（N10）/超重型（N120）不修正（表B.0.1 仅适用重型）
        is_heavy = _is_heavy_dpt(dtlx_raw)
        # V2.2.2（C4）：无显式 project_type 时回退模块级工程类型（RuleEngine 注入）
        if project_type is None:
            project_type = _DPT_PROJECT_TYPE
        # V3.0.2（A5/D）：A类（GB50021-2001 表B.0.1）与 B类（铁建 TB10041-2003
        # 重型动探修正表，合规审查确认与表B.0.1 同值）均用同一 DPT_ROD_ALPHA 矩阵
        # 双线性插值；库中无修正值（为0）时计算。
        # 杆长假设：理正动探表未单独存杆长字段，按 测试深度+偏移 近似（经验假设，
        # 偏移默认 2.0m，由 TOML「公用.动探杆长偏移」配置，可标定调整——
        # 合规审查 Agent3-S1/Agent1-10；与 actions.py _correct_dpt_for_hole 口径一致）。
        if project_type in ('A', 'B') and is_heavy and dtxzjs == 0 and dtjs > 0:
            from config import dpt_rod_correction_a
            alpha = dpt_rod_correction_a(dtdsd + dpt_rod_length_offset(), dtjs)
            # 修正结果保留浮点（C6），不再 round 成整数
            dtxzjs = dtjs * alpha
        return {'dtdsd': dtdsd,
                'dtlx': DTLX_MAP.get(dtlx_raw, dtlx_raw),
                'dtjs': dtjs,
                'dtxzjs': dtxzjs,
                'is_heavy': is_heavy}

    @staticmethod
    def _row_to_water(r, map_codes=True):
        """单条水位行 → dict（get_water_data / get_all_water 共用）

        map_codes=True 时 SWXZ/SWLX 映射为中文（单孔读取）；
        False 保留原始编码（批量读取，兼容按原始编码 '1' 判断稳定水位的调用方）。
        """
        swxz_raw = str(r.SWXZ).strip() if r.SWXZ is not None else ''
        swlx_raw = str(r.SWLX).strip() if r.SWLX is not None else ''
        row = {'swsd': DataAccess._safe_float(r.SWSD, 0),
               'swcsrq': str(r.SWCSRQ)[:10] if r.SWCSRQ else '',
               'swch': DataAccess._safe_int(r.SWCH, 0),
               'cy': DataAccess._safe_int(r.CY, 0)}
        if map_codes:
            row['swxz'] = SWXZ_MAP.get(swxz_raw, '其他')
            row['swlx'] = SWLX_MAP.get(swlx_raw, '其他')
        else:
            row['swxz'] = swxz_raw
            row['swlx'] = swlx_raw
        return row

    # ---- 读取 ----

    def get_all_boreholes(self):
        # P1-1：列探测——此前直接 SELECT 固定列，ZKLC/ZKPIL 缺失时 SQL 整表失败
        # → 返回 []（全部钻孔静默消失，KPI 卡/钻孔列表全空）；hasattr 回退是死代码
        # （AttrRow 属性=SELECT 列表，列缺失根本到不了行）。现按实际列组装 SELECT。
        cols = ['ZKBH', 'ZKX', 'ZKY', 'ZKSD', 'ZKBG']
        try:
            cur = self._safe_query('钻孔列探测',
                                   f"SELECT * FROM {self._t('z_ZuanKong')} WHERE 1=0")
            if cur and cur.description:
                actual = {c[0].upper() for c in cur.description}
                for c in ('ZKLC', 'ZKPIL'):
                    if c in actual:
                        cols.append(c)
        except Exception:
            _log_error(f'钻孔列探测失败: {traceback.format_exc()}')
        cursor = self._safe_query('钻孔列表',
            f"SELECT {', '.join(cols)} FROM {self._t('z_ZuanKong')} ORDER BY ZKBH")
        if cursor is None:
            return []
        rows = []
        for r in cursor.fetchall():
            rows.append({'zkbh': str(r.ZKBH).strip() if r.ZKBH else '',
                         'zkx': self._safe_float(r.ZKX, 0),
                         'zky': self._safe_float(r.ZKY, 0),
                         'zksd': self._safe_float(r.ZKSD, 0),
                         'zkbg': self._safe_float(r.ZKBG, 0),
                         'zklc': self._safe_float(r.ZKLC, 0) if hasattr(r, 'ZKLC') else 0,
                         'zkpil': self._safe_float(r.ZKPIL, 0) if hasattr(r, 'ZKPIL') else 0})
        return rows

    def get_strata(self, gcsy, zkbh):
        sql = f"""SELECT TCZCBH, TCYCBH, TCXH, TCCDSD, TCHD, TCYMC, TCMC, TCDZSD, TCDZCY,
                        TCYS, TCKSX, TCMSD, TCSID, TCFHCD, TCMS FROM {self._t('z_g_TuCeng')}
                 WHERE %s ZKBH = ? ORDER BY TCCDSD, TCXH""" % ('GCSY = ? AND' if gcsy else '')
        cursor = self._safe_query('地层数据', sql, [gcsy, zkbh] if gcsy else [zkbh])
        if cursor is None:
            return []
        return [self._row_to_stratum(r) for r in cursor.fetchall()]

    def get_test_data(self, zkbh):
        cursor = self._safe_query('试验数据',
            f"SELECT QYBH, QYSD, QYHSL, QYYX, QYSY, QYDC FROM {self._t('z_c_QuYang')} WHERE ZKBH = ? ORDER BY QYSD",
            [zkbh])
        if cursor is None:
            return []
        rows = []
        # P3（N+1）：先整孔批量取颗分/固结（修复前每行 2 次子查询）
        kefen_map = {}
        try:
            kf_cur = self.conn.cursor()
            kf_cur.execute(
                f"SELECT QYBH, kl20,kl10,kl5,kl2,kl1,kl_5,kl_25,kl_1,"
                f"kl_075,kl_074,kl_05,kl_01,kl_005,kl_002,kl0 "
                f"FROM {self._t('z_c_KeFen')} WHERE ZKBH=?", [zkbh])
            for kf in kf_cur.fetchall():
                try:
                    kefen_map[str(kf[0]).strip()] = _kefen_to_buckets(kf[1:])
                except Exception:
                    pass  # 单行异常不拖垮整批（Codex 复核 P1-5 逐行隔离）
            kf_cur.close()
        except Exception:
            _log_error(f'读取颗分失败 ZKBH={zkbh}: {traceback.format_exc()}')
        kxb_map = {}
        try:
            gj_col = self._probe_gujie_kxb_col()  # P1-2：GJKXBP0/GJKXBP0_ 变体探测
            if gj_col:
                gj_cur = self.conn.cursor()
                gj_cur.execute(
                    f"SELECT QYBH, [{gj_col}] FROM {self._t('z_c_GuJie')} WHERE ZKBH=?", [zkbh])
                for gj in gj_cur.fetchall():
                    if gj[1] is not None:
                        try:
                            kxb_map[str(gj[0]).strip()] = round(float(gj[1]), 3)
                        except Exception:
                            pass
                gj_cur.close()
        except Exception:
            _log_error(f'读取固结失败 ZKBH={zkbh}: {traceback.format_exc()}')

        for r in cursor.fetchall():
                hsl = self._opt_float(r.QYHSL)
                yx = self._opt_float(r.QYYX)
                sy = self._opt_float(r.QYSY)
                il, ip = self._calc_il_ip(hsl, yx, sy)
                qybh = str(r.QYBH).strip() if r.QYBH else ''
                kf_b = kefen_map.get(qybh)
                kxb = kxb_map.get(qybh)

                rows.append({
                    'qybh': qybh,
                    'qysd': self._safe_float(r.QYSD, 0),
                    'hsl': hsl, 'yx': yx, 'sx': sy,
                    'yxzs': il, 'sxzs': ip, 'kxb': kxb,
                    'qydc': str(r.QYDC).strip() if r.QYDC else '',
                    'r20_2': kf_b[0] if kf_b else None,
                    'r2_05': kf_b[1] if kf_b else None,
                    'r05_025': kf_b[2] if kf_b else None,
                    'r025_0075': kf_b[3] if kf_b else None,
                    'r0075': kf_b[4] if kf_b else None,
                })
        return rows

    def get_spt_data(self, zkbh):
        cursor = self._safe_query('标贯数据',
            f"SELECT BGDSD, BGGC, BGJS, BGXZJS FROM {self._t('z_y_BiaoGuan')} WHERE ZKBH = ? ORDER BY BGDSD",
            [zkbh])
        if cursor is None:
            return []
        return [self._row_to_spt(r) for r in cursor.fetchall()]

    def get_dpt_data(self, zkbh, project_type=None):
        cursor = self._safe_query('动探数据',
            f"SELECT DTDSD, DTLX, DTJS, DTXZJS FROM {self._t('z_y_DongTan')} WHERE ZKBH = ? ORDER BY DTDSD",
            [zkbh])
        if cursor is None:
            return []
        return [self._row_to_dpt(r, project_type) for r in cursor.fetchall()]

    def get_water_data(self, zkbh):
        cursor = self._safe_query('水位数据',
            f"SELECT SWSD, SWXZ, SWLX, SWCSRQ, SWCH, CY FROM {self._t('z_g_ShuiWei')} WHERE ZKBH = ? ORDER BY SWCH",
            [zkbh])
        if cursor is None:
            return []
        return [self._row_to_water(r, map_codes=True) for r in cursor.fetchall()]

    # ---- 批量读取（一次查询所有钻孔，返回 {zkbh: [rows]}） ----

    def get_all_strata(self):
        """批量读取所有钻孔地层，按 ZKBH 分组"""
        cursor = self._safe_query('地层数据(批量)',
            f"SELECT ZKBH, TCZCBH, TCYCBH, TCXH, TCCDSD, TCHD, TCYMC, TCMC, "
            f"TCDZSD, TCDZCY, TCYS, TCKSX, TCMSD, TCSID, TCFHCD, TCMS "
            f"FROM {self._t('z_g_TuCeng')} ORDER BY ZKBH, TCCDSD")
        if cursor is None: return {}
        result = {}
        for r in cursor.fetchall():
            zkbh = str(r.ZKBH).strip()
            result.setdefault(zkbh, []).append(self._row_to_stratum(r))
        return result

    def get_all_water(self):
        """批量读取所有水位数据，按 ZKBH 分组

        返回 {zkbh: [{swsd, swxz, ...}]}
        """
        cursor = self._safe_query('水位数据(批量)',
            f"SELECT ZKBH, SWSD, SWXZ, SWLX, SWCSRQ, SWCH, CY "
            f"FROM {self._t('z_g_ShuiWei')} ORDER BY ZKBH, SWCH")
        if cursor is None:
            return {}
        result = {}
        for r in cursor.fetchall():
            zkbh = str(r.ZKBH).strip()
            result.setdefault(zkbh, []).append(self._row_to_water(r, map_codes=False))
        return result

    def get_all_spt(self):
        """批量读取所有标贯，按 ZKBH 分组"""
        cursor = self._safe_query('标贯数据(批量)',
            f"SELECT ZKBH, BGDSD, BGGC, BGJS, BGXZJS FROM {self._t('z_y_BiaoGuan')} ORDER BY ZKBH, BGDSD")
        if cursor is None: return {}
        result = {}
        for r in cursor.fetchall():
            zkbh = str(r.ZKBH).strip()
            result.setdefault(zkbh, []).append(self._row_to_spt(r))
        return result

    def get_all_dpt(self, project_type=None):
        """批量读取所有动探，按 ZKBH 分组

        project_type: 'A'/'B'（None 时回退模块级工程类型，见 set_project_type）
        """
        cursor = self._safe_query('动探数据(批量)',
            f"SELECT ZKBH, DTDSD, DTLX, DTJS, DTXZJS FROM {self._t('z_y_DongTan')} ORDER BY ZKBH, DTDSD")
        if cursor is None: return {}
        result = {}
        for r in cursor.fetchall():
            zkbh = str(r.ZKBH).strip()
            result.setdefault(zkbh, []).append(self._row_to_dpt(r, project_type))
        return result

    def get_all_test(self):
        """批量读取所有试验，按 ZKBH 分组

        P0（全库复核漏颗分）：Step 2.5 批量联查 z_c_KeFen——修复前本函数不读颗分表，
        r20_2/r2_05/r05_025/r025_0075/r0075 硬编码 None，全库复核/问题导出的
        R-GRS-001 恒不触发，而单孔复核（get_test_data 读颗分）会触发，两入口结论
        不一致（实测孔 26-ZD-GZXT-1-1：单孔 3 条 vs 全库 2 条）。现与 get_test_data
        共用 _kefen_to_buckets 桶映射，批量一次查询消除 N+1。
        """
        # Step 1: 批量查询取样表（与 get_test_data 同源）
        cursor = self._safe_query('试验数据(批量)',
            f"SELECT ZKBH, QYBH, QYSD, QYHSL, QYYX, QYSY, QYDC "
            f"FROM {self._t('z_c_QuYang')} ORDER BY ZKBH, QYSD")
        if cursor is None:
            return {}

        # Step 2: 批量查询固结表（取孔隙比 kxb；P1-2：列名变体探测 GJKXBP0/GJKXBP0_）
        kxb_map = {}
        try:
            gj_col = self._probe_gujie_kxb_col()
            if gj_col:
                gj_cur = self.conn.cursor()
                gj_cur.execute(f"SELECT ZKBH, QYBH, [{gj_col}] FROM {self._t('z_c_GuJie')}")
                for gj in gj_cur.fetchall():
                    key = (str(gj[0]).strip(), str(gj[1]).strip())
                    try:
                        if gj[2] is not None:
                            kxb_map[key] = round(float(gj[2]), 3)
                    except Exception:
                        pass
                gj_cur.close()
        except Exception:
            pass

        # Step 2.5（P0）: 批量查询颗分表 → (zkbh, qybh) → 粒组统计桶
        kefen_map = {}
        try:
            kf_cur = self.conn.cursor()
            kf_cur.execute(
                f"SELECT ZKBH, QYBH, kl20,kl10,kl5,kl2,kl1,kl_5,kl_25,kl_1,"
                f"kl_075,kl_074,kl_05,kl_01,kl_005,kl_002,kl0 "
                f"FROM {self._t('z_c_KeFen')}")
            for kf in kf_cur.fetchall():
                key = (str(kf[0]).strip(), str(kf[1]).strip())
                try:
                    buckets = _kefen_to_buckets(kf[2:])
                    kefen_map[key] = buckets
                except Exception:
                    pass  # 单行异常不拖垮整批（Codex 复核 P1-5 逐行隔离）
            kf_cur.close()
        except Exception:
            pass

        result = {}
        for r in cursor.fetchall():
            zkbh = str(r.ZKBH).strip()
            hsl = self._opt_float(r.QYHSL)
            yx = self._opt_float(r.QYYX)
            sy = self._opt_float(r.QYSY)
            il, ip = self._calc_il_ip(hsl, yx, sy)
            qybh = str(r.QYBH).strip() if r.QYBH else ''
            kxb = kxb_map.get((zkbh, qybh), None)
            kf_b = kefen_map.get((zkbh, qybh))

            row = {
                'qybh': qybh,
                'qysd': self._safe_float(r.QYSD, 0),
                'hsl': hsl, 'yx': yx, 'sx': sy,
                'yxzs': il, 'sxzs': ip, 'kxb': kxb,
                'qydc': str(r.QYDC).strip() if r.QYDC else '',
                'r20_2': kf_b[0] if kf_b else None,
                'r2_05': kf_b[1] if kf_b else None,
                'r05_025': kf_b[2] if kf_b else None,
                'r025_0075': kf_b[3] if kf_b else None,
                'r0075': kf_b[4] if kf_b else None,
            }
            result.setdefault(zkbh, []).append(row)
        return result

    def _probe_gujie_kxb_col(self):
        """固结表孔隙比列探测（P1-2）：理正 8.5 用 GJKXBP0、9.0 变体用 GJKXBP0_，
        探测失败返回 None（调用方跳过 kxb，不再整块吞异常静默失效）"""
        try:
            cur = self._safe_query('固结列探测',
                                   f"SELECT * FROM {self._t('z_c_GuJie')} WHERE 1=0")
            if cur and cur.description:
                cols = [c[0].upper() for c in cur.description]
                for cand in ('GJKXBP0', 'GJKXBP0_'):
                    if cand in cols:
                        return cand
        except Exception:
            _log_error(f'固结表列探测失败: {traceback.format_exc()}')
        return None

    def get_all_test_full(self, project_type='B'):
        """批量读取所有土工试验数据（取样表 + 固结表 + 直剪表分别查询），按 ZKBH 分组

        project_type: 'A' 或 'B'，决定字段命名风格的兼容策略
        """
        t_qy = self._t('z_c_QuYang')
        t_gj = self._t('z_c_GuJie')
        t_zj = self._t('z_c_ZhiJian')

        # 字段命名策略：A类兼容 lizheng 9.0（有 _ 后缀），B类用标准名
        # 每种情况用降级查询（字段不存在时 SQL 报错→_safe_query 返回 None）
        if project_type == 'A':
            sql_primary = (
                f"SELECT ZKBH, QYBH, QYSD, QYHSL, QYYX, QYSY, QYZLMD, QYBZ, QYDC, QYKXB, "
                f"QYHSL_, QYYX_, QYSY_, QYZLMD_, QYBZ_, QYKXB_ "
                f"FROM {t_qy} ORDER BY ZKBH, QYSD")
            sql_fallback = (
                f"SELECT ZKBH, QYBH, QYSD, QYHSL, QYYX, QYSY, QYZLMD, QYBZ, QYDC "
                f"FROM {t_qy} ORDER BY ZKBH, QYSD")
        else:
            # B类
            sql_primary = (
                f"SELECT ZKBH, QYBH, QYSD, QYHSL, QYYX, QYSY, QYZLMD, QYBZ, QYDC "
                f"FROM {t_qy} ORDER BY ZKBH, QYSD")
            sql_fallback = sql_primary

        cursor = self._safe_query('试验数据(批量)', sql_primary)
        has_ext_fields = cursor is not None and project_type == 'A'
        if cursor is None:
            cursor = self._safe_query('试验数据(批量)', sql_fallback)
        if cursor is None:
            return {}

        result = {}
        # 取值辅助：主字段优先，若为 None 且允许后备则尝试 _ 后缀字段（提出循环，P3）
        def _val(r, col, fallback_col=None):
            v = getattr(r, col, None)
            if v is not None:
                try: return float(v)
                except: return v
            if fallback_col and has_ext_fields:
                v2 = getattr(r, fallback_col, None)
                if v2 is not None:
                    try: return float(v2)
                    except: return v2
            return None

        for r in cursor.fetchall():
            zkbh = str(r.ZKBH).strip()
            qybh = str(r.QYBH).strip() if r.QYBH else ''

            hsl = _val(r, 'QYHSL', 'QYHSL_')
            yx = _val(r, 'QYYX', 'QYYX_')
            sy = _val(r, 'QYSY', 'QYSY_')
            gmd_raw = _val(r, 'QYZLMD', 'QYZLMD_')
            gmd = round(gmd_raw * 9.81, 1) if gmd_raw is not None else None
            gs = _val(r, 'QYBZ', 'QYBZ_')
            # P1-4：e₀ 来源统一——不再从取样表 QYKXB 取（A 类此前用取样表、
            # B 类用固结表，同库 A/B 统计 e₀ 来源与数值不一致）；统一由固结表
            # GJKXBP0(-) 提供，下方固结块覆盖写入，取样表值仅作固结缺失兜底
            # （P1-6：恢复注释承诺的 QYKXB 兜底初始值，固结有值仍覆盖）
            kxb = _val(r, 'QYKXB', 'QYKXB_')

            il, ip = self._calc_il_ip(hsl, yx, sy)

            row = {
                'qybh': qybh,
                'qysd': self._safe_float(r.QYSD, 0),
                'hsl': hsl, 'yx': yx, 'sx': sy,
                'yxzs': il, 'sxzs': ip,
                'qydc': str(r.QYDC).strip() if r.QYDC else '',
                'gmd': gmd,
                'Gs': gs,
                'kxb': kxb,
                'alpha': None, 'Es': None, 'phi': None, 'cohesion': None,
            }
            result.setdefault(zkbh, []).append(row)

        # 2. 固结表自适应：按实际字段名匹配（8.5: GJXS0102/GJML0102  9.0: GJXSXM1/GJMLXM1）
        try:
            # 先查表结构确定字段名
            gj_cols = set()
            try:
                c_schema = self.conn.cursor()
                c_schema.execute(f"SELECT TOP 1 * FROM [{t_gj}]")
                gj_cols = {d[0].upper() for d in c_schema.description}
            except Exception as e:
                _log_error(f'固结表结构探测失败({t_gj}): {e}')

            gj_kxb = ('GJKXBP0' if 'GJKXBP0' in gj_cols
                      else 'GJKXBP0_' if 'GJKXBP0_' in gj_cols else None)
            # P1-3：修复前候选列全缺时回退到不存在的默认列名（GJXSXM1/GJMLXM1），
            # SELECT 必失败 → 整个固结块被 except 吞掉，kxb/alpha/Es 全丢。
            # 现候选全缺时置 None（该字段不参与 SELECT，其余字段仍可取）。
            gj_alpha = next((f for f in ['GJXSXM1', 'GJXS0102'] if f in gj_cols), None)
            gj_es = next((f for f in ['GJMLXM1', 'GJML0102'] if f in gj_cols), None)
            gj_fields = [f for f in (gj_kxb, gj_alpha, gj_es) if f]

            if gj_fields:
                c2 = self._safe_query('固结数据(批量)',
                    f"SELECT ZKBH, QYBH, {', '.join(gj_fields)} "
                    f"FROM {t_gj} ORDER BY ZKBH, QYBH")
                if c2:
                    for r in c2.fetchall():
                        key = (str(r.ZKBH).strip(), str(r.QYBH).strip())
                        alpha_val = self._safe_float(getattr(r, gj_alpha, None), None) if gj_alpha else None
                        es_val = self._safe_float(getattr(r, gj_es, None), None) if gj_es else None
                        kxb_val = self._safe_float(getattr(r, gj_kxb, None), None) if gj_kxb else None
                        if kxb_val is None: kxb_val = self._safe_float(getattr(r, 'GJKXBP0', None), None)
                        if kxb_val is None: kxb_val = self._safe_float(getattr(r, 'GJKXBP0_', None), None)
                        for d in result.get(key[0], []):
                            if d['qybh'] == key[1]:
                                # P1-4：固结表值无条件优先（覆盖取样表兜底值）
                                if kxb_val is not None:
                                    d['kxb'] = kxb_val
                                if alpha_val is not None:
                                    d['alpha'] = alpha_val
                                if es_val is not None:
                                    d['Es'] = es_val
        except Exception as e:
            _log_error(f'固结数据(批量)读取失败: {e}')

        # 3. 直剪表自适应：按 ZJSYFF 区分快剪(00) / 固快(10)，读取对应的 φ/c 字段
        try:
            zj_cols = set()
            try:
                c_schema = self.conn.cursor()
                c_schema.execute(f"SELECT TOP 1 * FROM [{t_zj}]")
                zj_cols = {d[0].upper() for d in c_schema.description}
            except Exception as e:
                _log_error(f'直剪表结构探测失败({t_zj}): {e}')

            zj_00phi = 'ZJNMJ00' if 'ZJNMJ00' in zj_cols else None
            zj_00coh = 'ZJNJL00' if 'ZJNJL00' in zj_cols else None
            zj_10phi = 'ZJNMJ10' if 'ZJNMJ10' in zj_cols else None
            zj_10coh = 'ZJNJL10' if 'ZJNJL10' in zj_cols else None
            # P2（直剪单侧）：无 00 列但有 10 列（理正变体）时回退用 10 列，
            # 不再整块跳过
            if not zj_00phi and zj_10phi:
                zj_00phi, zj_00coh = zj_10phi, zj_10coh

            if zj_00phi and zj_00coh:
                zj_select = f"ZKBH, QYBH, ZJSYFF, {zj_00phi}, {zj_00coh}"
                if zj_10phi and zj_10coh:
                    zj_select += f", {zj_10phi}, {zj_10coh}"

                c3 = self._safe_query('直剪数据(批量)',
                    f"SELECT {zj_select} FROM {t_zj} ORDER BY ZKBH, QYBH")
                if c3:
                    # ZJSYFF 试验方法编码：理正各版本混用数字/字母/中文
                    #   快剪: '' '0' '00' 'q' '快' '快剪' '直剪快剪'
                    #   固快: '1' '10' 'g' '固' '固快' '固结快剪'
                    kj_codes = ('', '0', '00', 'q', 'Q', '快', '快剪', '直剪快剪')
                    gk_codes = ('1', '10', 'g', 'G', '固', '固快', '固结快剪')
                    for r in c3.fetchall():
                        key = (str(r.ZKBH).strip(), str(r.QYBH).strip())
                        syff = str(r.ZJSYFF).strip() if r.ZJSYFF else ''
                        if syff in gk_codes and zj_10phi and zj_10coh:
                            phi_col, coh_col = zj_10phi, zj_10coh
                        else:
                            # 快剪编码与所有未知编码：默认取快剪(00)列
                            phi_col, coh_col = zj_00phi, zj_00coh
                        phi = self._safe_float(getattr(r, phi_col, None), None)
                        coh = self._safe_float(getattr(r, coh_col, None), None)
                        # 首选组为空但另一组有值时回退（兼容编码与实际列不一致）
                        if (phi is None and coh is None) and zj_10phi and zj_10coh:
                            if phi_col == zj_00phi:
                                alt_phi, alt_coh = zj_10phi, zj_10coh
                            else:
                                alt_phi, alt_coh = zj_00phi, zj_00coh
                            phi = self._safe_float(getattr(r, alt_phi, None), None)
                            coh = self._safe_float(getattr(r, alt_coh, None), None)
                        if phi is None and coh is None:
                            continue
                        for d in result.get(key[0], []):
                            if d['qybh'] == key[1]:
                                d['phi'] = phi
                                d['cohesion'] = coh
        except Exception as e:
            _log_error(f'直剪数据(批量)读取失败: {e}')

        return result

    # ---- 写入（单字段更新） ----

    # G1（V2.2.6）：岩土名称列探测——理正库存在 TCMC / TCYMC 两种列形态，
    # 读侧已 (TCMC or TCYMC) 优先，写侧必须与读侧同口径，否则 TCMC 型库改名称恒验证失败。
    # P2（跨库污染）：缓存从类级改为实例级——Web 端单进程可轮换多个工作库，
    # 类级缓存会在切换库后沿用上一库的列形态（TCMC 型库探测结果泄漏到 TCYMC 型库）。
    _stratum_name_col_cache = None  # 实例级占位（__init__ 中重置）

    def get_stratum_name_col(self):
        """返回该库岩土名称实际列名（'TCMC' 优先，否则 'TCYMC'）"""
        if self._stratum_name_col_cache is not None:
            return self._stratum_name_col_cache
        col = 'TCYMC'
        try:
            cur = self._safe_query('岩土名称列探测',
                                   f"SELECT * FROM {self._t('z_g_TuCeng')} WHERE 1=0")
            if cur and cur.description:
                cols = [c[0].upper() for c in cur.description]
                if 'TCMC' in cols:
                    col = 'TCMC'
        except Exception:
            _log_error(f'岩土名称列探测失败，回退 TCYMC: {traceback.format_exc()}')
        self._stratum_name_col_cache = col
        return col

    def update_stratum_field(self, zkbh, tcxh, field, value, commit=True):
        fu = field.upper()
        # G1：岩土名称列按库实际形态归一化（TCMC 型库写 TCMC，TCYMC 型库写 TCYMC）
        if fu in ('TCYMC', 'TCMC'):
            fu = self.get_stratum_name_col()
        if fu not in ALLOWED_FIELDS:
            raise ValueError(f'字段不允许: {field}')
        try:
            c = self.conn.cursor()
            c.execute(f"UPDATE {self._t('z_g_TuCeng')} SET [{fu}] = ? WHERE ZKBH = ? AND TCXH = ?", [value, zkbh, tcxh])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'更新地层失败: {e}') from e

    def update_spt_raw_and_clear_corrected(self, zkbh, bgdsd, bgjs, project_type='B', commit=True):
        """更新标贯原始击数，并按工程类型对应的杆长修正表计算修正击数

        V3.0.2（A4/C1）：A类（GB50021，L≥21m 封 0.70）与 B类（铁建 3~30m）
        分别接线各自修正表；此前 A类误用 B类铁建表，深孔（>21m）修正值
        系统性偏低 3%~10%（A类专用 config.spt_correction_gb50021 为死代码）。
        """
        try:
            c = self.conn.cursor()
            # 先查杆长用于修正系数计算
            c.execute(f"SELECT BGGC FROM {self._t('z_y_BiaoGuan')} WHERE ZKBH = ? AND BGDSD = ?", [zkbh, bgdsd])
            row = c.fetchone()
            bggc = float(row[0]) if row and row[0] is not None else 0
            if project_type == 'A':
                from config import spt_correction_gb50021
                a = spt_correction_gb50021(bggc)
            else:
                a = _spt_correction_coefficient(bggc)
            bgxzjs = round(bgjs * a, 1) if a > 0 else None
            if bgxzjs is not None:
                c.execute(f"UPDATE {self._t('z_y_BiaoGuan')} SET BGJS = ?, BGXZJS = ? WHERE ZKBH = ? AND BGDSD = ?",
                          [bgjs, bgxzjs, zkbh, bgdsd])
            else:
                c.execute(f"UPDATE {self._t('z_y_BiaoGuan')} SET BGJS = ?, BGXZJS = NULL WHERE ZKBH = ? AND BGDSD = ?",
                          [bgjs, zkbh, bgdsd])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'更新标贯失败: {e}') from e

    def update_dpt_raw_and_clear_corrected(self, zkbh, dtdsd, dtjs, project_type='B', commit=True):
        """更新动探原始击数；重型动探按工程类型自动重算修正值（浮点），
        轻型/超重型行清空修正值（不参与 N63.5 修正）

        V3.0.2（A5/D + B4 + B5）：此前仅更新 DTJS 不重算/清理 DTXZJS（stale
        修正值继续参与 R-DEN-002）；B类此前从不计算修正值 → 密实度回退实测
        N63.5 查"修正后"口径表（系统性偏高估一档）。
        修正依据：A类 GB50021-2001 表B.0.1；B类铁建 TB10041-2003 重型动探修正表
        与表B.0.1 同值（合规审查确认），复用 config.dpt_rod_correction_a 双线性插值。
        杆长假设：测试深度+偏移（偏移由 TOML「公用.动探杆长偏移」配置，默认 2.0m，
        与 dao._row_to_dpt / actions.py 口径一致；合规审查 Agent3-S1）。
        """
        try:
            c = self.conn.cursor()
            c.execute(f"SELECT DTLX FROM {self._t('z_y_DongTan')} WHERE ZKBH = ? AND DTDSD = ?", [zkbh, dtdsd])
            row = c.fetchone()
            dtlx_raw = str(row[0]).strip() if row and row[0] is not None else ''
            if _is_heavy_dpt(dtlx_raw) and project_type in ('A', 'B') and dtjs > 0:
                from config import dpt_rod_correction_a
                alpha = dpt_rod_correction_a(dtdsd + dpt_rod_length_offset(), dtjs)
                # V3.0.2（B5）：修正值按浮点写入（10.4 不 int 化），与读取/直判同口径
                dtxzjs = dtjs * alpha
                c.execute(f"UPDATE {self._t('z_y_DongTan')} SET DTJS = ?, DTXZJS = ? WHERE ZKBH = ? AND DTDSD = ?",
                          [dtjs, dtxzjs, zkbh, dtdsd])
            else:
                c.execute(f"UPDATE {self._t('z_y_DongTan')} SET DTJS = ?, DTXZJS = NULL WHERE ZKBH = ? AND DTDSD = ?",
                          [dtjs, zkbh, dtdsd])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'更新动探失败: {e}') from e

    def update_dpt_corrected_only(self, zkbh, dtdsd, dtxzjs, commit=True):
        """单独更新动探修正击数（用户手动设置），不修改原始值

        V3.0.2（B5/C6）：修正值按浮点写入（10.4 不再 int 化为 10；
        非整数原本被 str().isdigit() 误判为非法而存 NULL，一并修复）。
        """
        try:
            try:
                val = float(dtxzjs)
            except (ValueError, TypeError):
                val = None
            c = self.conn.cursor()
            c.execute(f"UPDATE {self._t('z_y_DongTan')} SET DTXZJS = ? WHERE ZKBH = ? AND DTDSD = ?",
                      [val, zkbh, dtdsd])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'更新动探修正值失败: {e}') from e

    def update_water_field(self, zkbh, swch, field, value, commit=True):
        if field not in ('SWSD', 'SWXZ', 'SWLX', 'CY', 'SWCSRQ', 'SWCH'):
            raise ValueError(f'水位字段不允许: {field}')
        try:
            c = self.conn.cursor()
            c.execute(f"UPDATE {self._t('z_g_ShuiWei')} SET [{field}] = ? WHERE ZKBH = ? AND SWCH = ?", [value, zkbh, swch])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'更新水位失败: {e}') from e

    # ---- INSERT / DELETE ----

    def insert_stratum(self, zkbh, layer_dict, commit=True):
        fields = ['ZKBH', 'TCXH']; values = [zkbh, layer_dict.get('tcxh', 0)]
        field_map = {'tczcbh': 'TCZCBH', 'tcycbh': 'TCYCBH', 'tcdzsd': 'TCDZSD', 'tcdzcy': 'TCDZCY',
                     'tccdsd': 'TCCDSD', 'tchd': 'TCHD', 'tcymc': self.get_stratum_name_col(), 'tcys': 'TCYS',
                     'tcmsd': 'TCMSD', 'tcksx': 'TCKSX', 'tcsid': 'TCSID', 'tcfhcd': 'TCFHCD', 'tcms': 'TCMS'}
        for key, dbf in field_map.items():
            val = layer_dict.get(key, '')
            # P3：tcmc 键兜底——调用方可能只传 'tcmc'（岩土名称异名），
            # 与读侧 _row_to_stratum 的 (TCMC or TCYMC) 同源口径一致
            if val in (None, '') and key == 'tcymc':
                val = layer_dict.get('tcmc', '')
            if val or val == 0 or val == 0.0:
                fields.append(dbf)
                values.append(val)
        sql = f"INSERT INTO {self._t('z_g_TuCeng')} ({','.join(f'[{f}]' for f in fields)}) VALUES ({','.join(['?'] * len(fields))})"
        try:
            c = self.conn.cursor(); c.execute(sql, values)
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'插入地层失败: {e}') from e

    def delete_stratum(self, zkbh, tcxh, commit=True):
        try:
            c = self.conn.cursor()
            c.execute(f"DELETE FROM {self._t('z_g_TuCeng')} WHERE ZKBH = ? AND TCXH = ?", [zkbh, tcxh])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'删除地层失败: {e}') from e

    def insert_spt(self, zkbh, bgdsd, bggc, bgjs, commit=True):
        try:
            c = self.conn.cursor()
            c.execute(f"INSERT INTO {self._t('z_y_BiaoGuan')} (ZKBH, BGDSD, BGGC, BGJS, BGXZJS) VALUES (?, ?, ?, ?, NULL)",
                      [zkbh, bgdsd, bggc, bgjs])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'插入标贯失败: {e}') from e

    def delete_spt(self, zkbh, bgdsd, commit=True):
        try:
            c = self.conn.cursor()
            c.execute(f"DELETE FROM {self._t('z_y_BiaoGuan')} WHERE ZKBH = ? AND BGDSD = ?", [zkbh, bgdsd])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'删除标贯失败: {e}') from e

    def insert_dpt(self, zkbh, dtdsd, dtlx, dtjs, commit=True):
        try:
            c = self.conn.cursor()
            c.execute(f"INSERT INTO {self._t('z_y_DongTan')} (ZKBH, DTDSD, DTLX, DTJS, DTXZJS) VALUES (?, ?, ?, ?, NULL)",
                      [zkbh, dtdsd, dtlx, dtjs])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'插入动探失败: {e}') from e

    def delete_dpt(self, zkbh, dtdsd, commit=True):
        try:
            c = self.conn.cursor()
            c.execute(f"DELETE FROM {self._t('z_y_DongTan')} WHERE ZKBH = ? AND DTDSD = ?", [zkbh, dtdsd])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'删除动探失败: {e}') from e

    def insert_water(self, zkbh, swch, swsd, swxz_code, swlx_code, cy, swcsrq='', commit=True):
        try:
            c = self.conn.cursor()
            c.execute(f"INSERT INTO {self._t('z_g_ShuiWei')} (ZKBH, SWCH, SWSD, SWXZ, SWLX, CY, SWCSRQ) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [zkbh, swch, swsd, swxz_code, swlx_code, cy, swcsrq or None])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'插入水位失败: {e}') from e

    def delete_water(self, zkbh, swch, commit=True):
        try:
            c = self.conn.cursor()
            c.execute(f"DELETE FROM {self._t('z_g_ShuiWei')} WHERE ZKBH = ? AND SWCH = ?", [zkbh, swch])
            if commit: self.conn.commit()
        except Exception as e:
            if commit: self.conn.rollback()
            raise RuntimeError(f'删除水位失败: {e}') from e
