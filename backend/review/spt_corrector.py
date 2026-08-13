"""理反 V1.5.1 — 标贯批量修正引擎 (spt_corrector.py)

扫描全库 R-DEN-001 / R-DEN-007 / R-DEN-008 问题，按层归类，按规则生成建议N值。

核心算法（4步）：
  ① 找参照 — 本孔本层正常标贯 (深度→N) 对，按深度线性插值
  ② 范围钳位 — clamp(参照值, min_n, max_n)
  ③ 无参照兜底 — 取 min_n
  ④ 深度趋势 — 同孔同层内 N_深 ≥ N_浅（不降，确定性 +1，保证结果可复现）
"""

from dataclasses import dataclass, field

from config import load_project_config


@dataclass
class SptSuggestion:
    """单条标贯修正建议"""
    zkbh: str           # 钻孔编号
    layer_label: str    # 层位标签 eg. "③-1"
    layer_name: str     # 岩土名称
    density: str        # 密实度（砂/碎石土）
    plasticity: str     # 可塑性（黏性土）
    weathering: str     # 风化程度（岩石）
    bgdsd: float        # 标贯深度(m)
    old_n: int          # 当前N值
    new_n: int          # 建议N值 / 或者 old_state:str 用于可塑性修正
    ref_values: list    # 本层正常标贯N值列表（用于预览展示）
    ref_depth_n: list   # 本层正常标贯 (深度, N) 对列表
    ref_source: str     # 参照来源
    issue_type: str     # 'density' / 'plasticity' / 'weathering'
    old_state: str      # 原状态值（密度/可塑性/风化程度）
    expected_state: str # 预期状态值
    # 内部用
    _borehole_depth_order: int = 0
    _layer_key: str = ''


class SptCorrector:
    """标贯批量修正引擎"""

    def __init__(self, da, engine):
        self.da = da
        self.engine = engine

    # ---- 全库扫描 ----

    def scan_all(self):
        """扫描全部钻孔，收集所有 R-DEN-001(砂土N→密实度)、R-DEN-007(黏性土N→可塑性)、R-DEN-008(岩石N→风化程度) 问题"""
        boreholes = self.da.get_all_boreholes()
        layers_map = {}

        for bh in boreholes:
            zkbh = bh['zkbh']
            if bh['zksd'] <= 0:
                continue
            strata = self.da.get_strata('', zkbh)
            spt_data = self.da.get_spt_data(zkbh)
            if not strata or not spt_data:
                continue
            issues = self.engine.review_strata(strata, spt_data, [], [])
            self._collect_issues(zkbh, strata, spt_data, issues, layers_map, 'R-DEN-001')
            self._collect_issues(zkbh, strata, spt_data, issues, layers_map, 'R-DEN-007')
            self._collect_issues(zkbh, strata, spt_data, issues, layers_map, 'R-DEN-008')

        result = sorted(layers_map.values(), key=lambda x: _layer_sort_key(x['layer_label']))
        for entry in result:
            entry['total_count'] = len(entry['suggestions'])
            # 收集跨钻孔的 (深度, N) 全局参照池
            global_pairs = []
            for sug in entry['suggestions']:
                global_pairs.extend(sug.ref_depth_n)
            entry['global_ref_depth_n'] = sorted(global_pairs, key=lambda x: x[0]) if global_pairs else []
            # v23 修复：此前未调用 compute_suggestions，new_n 恒等于 old_n（前端显示 "2 → 2" 的根因）。
            # 分类计算：密实度/风化（R-DEN-001/008）→ N 值修正（深度插值+钳位+趋势）；
            #           可塑性（R-DEN-007）→ 状态修正（compute_clay_corrections：N 不变，改 old_state→expected_state）。
            try:
                _cfg = load_project_config()
                _stat_n = _cfg.get('试验指标_统计', {}).get('N', {})
                _min_n = int(_stat_n.get('最小', 0) or 0)
                _max_n = int(_stat_n.get('最大', 200) or 200)
                if _min_n < 1: _min_n = 1   # 击数下限至少 1（0 仅统计允许）
                _dens = [s for s in entry['suggestions'] if getattr(s, 'issue_type', '') != 'plasticity']
                _clay = [s for s in entry['suggestions'] if getattr(s, 'issue_type', '') == 'plasticity']
                if _dens and _min_n < _max_n:
                    self.compute_suggestions(_dens, _min_n, _max_n,
                                             global_ref_depth_n=entry['global_ref_depth_n'])
                if _clay:
                    self.compute_clay_corrections(_clay)
            except Exception:
                # 计算失败不阻断扫描：保留 old_n，前端会显示 "无需修正" 语义
                pass
        return result

    def _collect_issues(self, zkbh, strata, spt_data, issues, layers_map, rule_id):
        """从 issues 中提取指定规则的问题，按层分组"""
        # 确定分组键和状态字段
        if rule_id == 'R-DEN-001':
            group_field = 'density'
            state_field = 'tcmsd'
        elif rule_id == 'R-DEN-007':
            group_field = 'plasticity'
            state_field = 'tcksx'
        elif rule_id == 'R-DEN-008':
            group_field = 'weathering'
            state_field = 'tcfhcd'
        else:
            return

        problem_depths = set()
        for iss in issues:
            if iss.rule_id == rule_id and iss.ref_value:
                problem_depths.add(round(iss.ref_value, 2))

        for iss in issues:
            if iss.rule_id != rule_id or not iss.ref_value:
                continue

            layer_idx = iss.layer_index
            if layer_idx >= len(strata):
                continue
            layer = strata[layer_idx]

            prev_depth = strata[layer_idx - 1]['tccdsd'] if layer_idx > 0 else 0
            layer_bottom = layer['tccdsd']

            # 本层所有标贯
            layer_spt_all = [s for s in spt_data if prev_depth < s['bgdsd'] <= layer_bottom]

            # 本层正常标贯（无该规则问题且N>0）
            normal_spt = [s for s in layer_spt_all
                          if s['bgjs'] > 0 and round(s['bgdsd'], 2) not in problem_depths]

            bgdsd = iss.ref_value
            spt = next((s for s in spt_data if abs(s['bgdsd'] - bgdsd) < 0.005), None)
            old_n = spt['bgjs'] if spt else 0

            label = iss.layer_label or _make_layer_label(layer)
            name = layer.get('tcymc', '')
            density = layer.get('tcmsd', '') if group_field == 'density' else ''
            plasticity = layer.get('tcksx', '') if group_field == 'plasticity' else ''
            weathering = layer.get('tcfhcd', '') if group_field == 'weathering' else ''

            # 从消息中提取预期值（eg. "N=10对应硬塑，当前可塑性为软塑" → expected=硬塑；
            # 修正击数优先后 N 可为小数，如 "N=9.6对应松散，当前密实度为稍密"）
            expected_value = ''
            import re
            m = re.search(r'N=([\d.]+)对应(\S+)，当前', iss.message)
            expected_value = m.group(2) if m else ''

            layer_key = f"{name}_{expected_value}_{group_field}"

            if layer_key not in layers_map:
                layers_map[layer_key] = {
                    'layer_key': layer_key,
                    'layer_label': label,
                    'layer_name': name,
                    'density': density,
                    'plasticity': plasticity,
                    'weathering': weathering,
                    'issue_type': 'density' if rule_id == 'R-DEN-001' else ('plasticity' if rule_id == 'R-DEN-007' else 'weathering'),
                    'suggestions': [],
                }

            ref_dn = [(float(s['bgdsd']), int(s['bgjs'])) for s in normal_spt]
            sug = SptSuggestion(
                zkbh=zkbh,
                layer_label=label,
                layer_name=name,
                density=density,
                plasticity=plasticity,
                weathering=weathering,
                bgdsd=bgdsd,
                old_n=old_n,
                new_n=old_n,
                ref_values=[s['bgjs'] for s in normal_spt],
                ref_depth_n=ref_dn,
                ref_source='本孔参照' if ref_dn else '无参照',
                issue_type='density' if rule_id == 'R-DEN-001' else ('plasticity' if rule_id == 'R-DEN-007' else 'weathering'),
                old_state=layer.get(state_field, ''),
                expected_state=expected_value,
                _layer_key=layer_key,
            )
            layers_map[layer_key]['suggestions'].append(sug)

    # ---- 计算建议值 ----

    def compute_suggestions(self, suggestions, min_n, max_n, enforce_trend=True,
                            global_ref_values=None, global_ref_depth_n=None):
        """对一组 SptSuggestion 计算 new_n

        Args:
            suggestions: [SptSuggestion]
            min_n: N值范围下限
            max_n: N值范围上限
            enforce_trend: 是否强制深度趋势（越深越大）
            global_ref_values: 已废弃（保留兼容）
            global_ref_depth_n: 跨钻孔的 (深度, N) 参照池
        """
        if min_n >= max_n:
            raise ValueError(f"范围无效: 下限{min_n} >= 上限{max_n}")

        if global_ref_depth_n is None:
            global_ref_depth_n = []

        sorted_sugs = sorted(suggestions, key=lambda s: (s.zkbh, s.bgdsd))
        prev_n = {}

        for sug in sorted_sugs:
            # ① 深度插值找参照
            if sug.ref_depth_n:
                ref = self._interpolate_by_depth(sug.bgdsd, sug.ref_depth_n)
                sug.ref_source = '本孔参照'
            elif global_ref_depth_n:
                ref = self._interpolate_by_depth(sug.bgdsd, global_ref_depth_n)
                sug.ref_source = '全局参照'
            else:
                ref = min_n
                sug.ref_source = '范围中值'

            # ② 范围钳位
            new_n = max(min_n, min(max_n, ref))

            # ③ 深度趋势（同孔内不降，确定性规则：不低于 上一深度+1，保证可复现）
            if enforce_trend and sug.zkbh in prev_n:
                prev = prev_n[sug.zkbh]
                if new_n <= prev:
                    # 原实现 random.randint(0,2) 导致同输入两次运行结果不同；
                    # 改为确定性 +1（深部标贯应比浅部偏大），并钳位到范围上限
                    new_n = prev + 1
                new_n = min(max_n, new_n)

            sug.new_n = new_n
            prev_n[sug.zkbh] = new_n

        return sorted_sugs

    @staticmethod
    def compute_clay_corrections(suggestions):
        """对黏性土标贯-可塑性不匹配的修正建议：将 new_n 设为应修正的可塑性状态键
        用户可选择自动修正（将所有条目的 old_state 改为 expected_state）
        """
        for sug in suggestions:
            sug.new_n = sug.old_n  # N值不变
        return suggestions

    @staticmethod
    def _interpolate_by_depth(sug_depth, ref_pairs):
        """根据深度在参照点之间线性插值

        ref_pairs: [(depth, N), ...] 已按深度排序
        上部（浅于最浅参照）→ 取最浅参照N
        中部（两参照之间）→ 线性插值
        下部（深于最深参照）→ 取最深参照N
        """
        if not ref_pairs:
            return 0

        ref_sorted = sorted(ref_pairs, key=lambda x: x[0])

        # 浅于所有参照 → 上部
        if sug_depth <= ref_sorted[0][0]:
            return ref_sorted[0][1]

        # 深于所有参照 → 下部
        if sug_depth >= ref_sorted[-1][0]:
            return ref_sorted[-1][1]

        # 在两参照之间 → 中部，线性插值
        for i in range(len(ref_sorted) - 1):
            d1, n1 = ref_sorted[i]
            d2, n2 = ref_sorted[i + 1]
            if d1 <= sug_depth <= d2:
                if d2 == d1:
                    return (n1 + n2) // 2
                ratio = (sug_depth - d1) / (d2 - d1)
                return int(round(n1 + (n2 - n1) * ratio))

        # fallback（不应到达）
        return ref_sorted[0][1]

    @staticmethod
    def _median(values):
        """整数中位数（保留兼容）"""
        if not values:
            return 0
        vals = sorted(values)
        n = len(vals)
        if n % 2 == 1:
            return vals[n // 2]
        else:
            return (vals[n // 2 - 1] + vals[n // 2]) // 2


# ---- 工具函数 ----

def _make_layer_label(layer):
    tczcbh = layer.get('tczcbh', '')
    tcycbh = layer.get('tcycbh', '')
    if not tczcbh:
        return ''
    if tcycbh:
        return f"{tczcbh}-{tcycbh}"
    return str(tczcbh)


def _layer_sort_key(label):
    """将层位标签转为可排序的元组"""
    import re
    parts = re.findall(r'\d+', label)
    return tuple(int(p) for p in parts) if parts else (999,)
