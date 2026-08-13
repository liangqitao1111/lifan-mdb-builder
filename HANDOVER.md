# 理反 Web · 交接文档（HANDOVER）

> 更新：2026-08-13 13:10 · 服务地址：http://127.0.0.1:8766/
> 最新提交：`b120f35`（main）· 另有 5 个文件**未提交**（见 §9）
> 代码规模：`index.html` 2749 行（v31 清理后）/ 后端 FastAPI

---

## 1. 项目概览

**项目**：理反 Web — 将桌面版「理反 V3.0.4 理正地层复核工具」网页化。
**定位**：围绕理正勘察数据库（.mdb/.lz）做复核/编辑/统计/导出，页面以**桌面版为口径**（列顺序/列名/完整性一致），修复 bug 不得丧失特色优化。

| 项 | 值 |
|---|---|
| 仓库 | `C:\Users\神舟\WorkBuddy\2026-08-12-01-18-03\lifan_web` |
| 远端 | https://github.com/liangqitao1111/lifan-mdb-builder.git（main） |
| 技术栈 | 单文件前端 `index.html`（原生 JS）+ FastAPI 后端（`backend/`，SQLite 工作库） |
| 账号 | admin / admin（全部 API 需 Bearer token） |
| 测试库 | `work/dbs/e2e_real.lz`（85 表 / 408 孔 / 土工 616 行 / 颗分 z_c_KeFen 214 行） |
| 桌面入口 | `C:\Users\神舟\Desktop\理反Web 一键启动.bat` |

## 2. 运行方式

```powershell
# ⚠️ 必须用 envs/default 环境（托管裸解释器 versions\3.13.12 没有 uvicorn！）
cd C:\Users\神舟\WorkBuddy\2026-08-12-01-18-03\lifan_web
"C:\Users\神舟\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8766 --app-dir backend
# 浏览器打开 http://127.0.0.1:8766/ ，admin/admin 登录
```

- main.py 在 `backend/` 目录，启动必须带 `--app-dir backend`。
- **改过后端代码必须重启**（uvicorn 无热重载）；前端 `index.html` 是静态文件，刷新即生效。
- 健康检查：`curl http://127.0.0.1:8766/api/health` → `{"status":"ok","mdb_backend":"ace"}`。
- 桌面双击 `理反Web 一键启动.bat` = 启动服务 + 自动打开浏览器（start.bat 已修：envs/default + 8766 端口）。

## 3. 版本历史（8/12 基础版 → 8/13 v1~v32）

### Git 提交（8/12，基础功能）
| 提交 | 内容 |
|---|---|
| `b120f35` | 交接文档 HANDOVER（首版） |
| `6ef6744` | 数据表交互增强 — 表头排序(白名单)/列筛选/冻结前2列/长文本截断/键盘导航/行高52px/CY状态色 |
| `5fbb3b7` | 人性化排版 — 操作列固定90px/描述列后置/土工17列/sticky表头+右对齐+斑马纹 |
| `2e4529a` | 地层页排版/行内编辑 + 参数中心状态定义 + 下载鉴权 401 |
| `ac2463d` | P3 体验 — 深色模式/移动端/列显隐/E2E 固化 |
| `e2cf6d0` | P2 部署 — 一键启动/Docker/Caddy/README |
| `62694e4` | P1 功能 — 标贯一键应用/动探修正/问题导出/表头映射 |
| `ea2ed26` | P0 安全 — Token 鉴权 + 操作审计日志 |

### 迭代版本（8/12 晚 ~ 8/13，**尚未提交**，见 §9）
| 版本 | 内容 |
|---|---|
| **v1** | 地层页/整体 UI 优化：统计卡 hover 动效+语义色圆点、三栏 270/1fr/340、深色模式表格 hover 适配、Cmd/Ctrl+K 聚焦搜索 + Esc 清空、skeleton 骨架屏、seg-tabs 分段控件、toast.info、btn.loading、CSS 设计令牌（--space/--fs/--radius/--shadow/--ease） |
| **v2** | 冻结列默认关闭（列随滚动）+ 列设置加"冻结前两列"开关；表头/数据统一居中 |
| **v3** | 6 项：地层序号单孔化（TCXH）；列宽按类型 COL_WIDTH 表；操作列去"编辑"（双击行内编辑）；干密度/孔隙比空值诊断（源库空，非 bug）；水位 CY 改下拉选择栏（cyChange 保存 1/0）；层号显示主-亚（TCZCBH-TCYCBH） |
| **v4** | 地层序号按单孔层位埋深排序（ZKBH + TCCDSD 升序，# 列孔内 1..N 重算，空值沉底） |
| **v5** | 三栏瘦身 190/1fr/270 + 土工颗分独立页签（伪表 `z_c_QuYang__kf`）+ 表头换行 + **伪表机制 `wsRealTable()`**（解析 `__` 前缀，6 处 API URL 兼容） |
| **v6** | 弹性列宽 `minmax(min,1fr)`（窄屏不滚动/宽屏拉伸）+ 描述/定名 2fr + 操作列固定 50px + 复核问题列 270→240；修复 `pxpx` 模板 bug |
| **v7** | 左侧 sidebar 可折叠：默认 56px 只显图标（main 多 184px）、hover 临时展开、click 持久展开、localStorage 记忆 |
| **v8** | 属性列固定紧凑 + 描述列独占剩余（描述 `minmax(156,1fr)`，属性列不参与弹性） |
| **v9** | 层厚列（TCHD）默认隐藏（列设置可恢复）+ 描述列封顶 `minmax(156,260px)` |
| **v10** | 修复单孔复核问题不响应「土工判别」开关——`wsSelectHole` 裸路径 → `wsReviewHoleQuery(zk)`（带全部参数） |
| **v11** | 开关组件双圆点 bug：旧 `.switch::after` 限定 `.switch-row .switch::after`（仅备份页 div 结构） |
| **v12** | **所有页签单孔维度**（WS.zkbh 主控，wsSetTab 强制 ZKBH 精确筛选）+ 钻孔列表单选修复（data-zk 精确匹配，修复 ZK-1/ZK-10 前缀误高亮） |
| **v13** | 所有页签序号单孔化（新增 DEPTH_COL 表）+ 颗分链路诊断（当时误判数据缺失） |
| **v14** | 修复「切换库」不跳上传页：`clearApiState();location.hash='#workspace';renderWorkspace()` |
| **v15** | 钻孔列表展示全部孔（去掉 `clean.slice(0,40)` 截断） |
| **v16** | **颗分映射修复（重要）**：真实颗分在独立表 **`z_c_KeFen`**（214 行真实数据），不在 z_c_QuYang 的 QYZY 列（源库空）。颗分 tab 从伪表改指真表；g_TableName 有中文表名索引 |
| **v17** | 清理 v3~v16 版本注释 + 钻孔列表自然序（去 risky/clean 分段，全部孔 `localeCompare(numeric)` 一次排序，风险用徽标标注） |
| **v18** | **问题卡片→表格行定位高亮**：后端 `_issue_to_dict` 透传 `layer_index/ref_value`；前端卡片加 data-idx + `jumpToIssue()`（await 加载 + 轮询 + scrollIntoView + 闪烁动画 rowFlashAnim，桌面/移动端） |
| **v19** | 取消复核页（工作台）搜索框（与顶部 globalSearch 重复）+ 钻孔列表「仅问题孔」开关 + KPI 卡点击跳转（wsKpiJump） |
| **v20** | KPI 4→3 卡去重（删"高风险问题"卡，H/M 细分并入 sub）+ 钻孔列表 UI（渐变 active/风险色条/圆角）+ 列设置弹窗过滤系统列与 `_` 标志位列、中文映射全覆盖 |
| **v21** | 标贯修正深度浮点 `fmtNum` + 钻孔列表防跨行拥挤（ellipsis+nowrap）+ **问题卡片按规则类型跳页签**（RULE_TAB 映射 + ref_value 定位，标贯→z_y_BiaoGuan 等） |
| **v22** | 表头不居中修复：`.t-head .col-sort` 加 `justify-content:center`（flex:1 子项无视父容器 justify-content） |
| **v23** | 标贯修正击数标注：后端 `scan_all` **激活 compute_suggestions**（此前从未调用，new_n 恒等 old_n）+ 分类处理（R-DEN-007 可塑性走 compute_clay_corrections N 不变）；前端"原始击数→修正击数"标注 |
| **v24** | 「修正击数」改「**修改后击数**」+ 双击编辑（sptEditN）；**跨层标贯规则 R-SPT-001**（标贯深度不在任何地层 (层顶,层底] 区间→H 级，优先级高于击数规则，单测 7 用例全过）；参数中心卡片化 |
| **v25** | 参数中心系统性排版（UI 专家）：设计系统 cfg-panel/cfg-sub/cfg-card/cfg-note/cfg-state-table，段色点 nth-child 轮换、计数徽标、响应式 720px 单列 |
| **v26** | 参数中心「合理填满不留空」：auto-fill→**auto-fit** + 段网格 2 列 minmax(520px) + 字段 3 列 minmax(200px)（段/字段最小宽需协同设计） |
| **v27** | 用途字段只读（disabled + saveConfig 跳过）+ 卡片布局灵活（默认横排，textarea 自动 .col 竖向）+ 注释清理 |
| **v28** | 段网格改**瀑布流 `columns:2`**（根治 grid 行高留白，break-inside:avoid + margin-bottom 14px） |
| **v29** | 岩溶统计按项目类型自动选择（`WS_SETTINGS.project_type` 决定 karst_a/karst_b，不再并列） |
| **v30** | 全局注释专业化清理（19 处版本号注释，保留简洁模块说明） |
| **v31** | 移除**全部注释** + 删除 215 行视觉粗糙 dead code（renderReview/renderBorehole/renderSpt 假数据页，route 归一化后不可达）；3023→2759 行 |
| **v32** | 生成 MDB 页删除教学性文字（顶部副标题/4 步流程指示器/日志 API 路径说明），保留核心按钮与状态 |

## 4. 当前功能清单（v32 状态）

### 4.1 数据工作台
- **KPI 3 卡**：总孔数 / 风险钻孔（sub: H 级孔 x · M 级孔 y）/ 问题总数（sub: H 级 x · M 级 y），点击跳转对应问题孔（wsKpiJump）
- **钻孔列表**：全部孔自然序（localeCompare numeric）、单选（data-zk 精确）、「仅问题孔」过滤开关、H 红条/M 黄条/徽标、hover 右滑、active 渐变
- **6 个页签**：地层 / 土工试验 / **颗分试验（z_c_KeFen）** / 动探 / 标贯 / 水位，**全部单孔维度**（WS.zkbh 联动）
- 表格：# 列孔内序号（1..N 跨孔重置）、层号主-亚、列数据居中、属性列紧凑+描述弹性（封顶 260）、操作列固定 46px、无横向滚动（1440）
- 行内编辑：双击/点击单元格 → input 保存（inlineEdit）；水位 CY 下拉；列设置（过滤系统列/标志位列，中文映射全覆盖）
- 搜索：顶部 globalSearch；表格搜索框已删（v19）；搜索 Enter 全局、清空/Esc 恢复单孔

### 4.2 复核问题面板
- 右侧问题卡片：H/M 等级底色、rule_id + message + field
- **点击卡片 → 自动跳对应页签**（RULE_TAB 映射）+ 定位行（地层按 layer_index，标贯/动探按 ref_value 深度容差 0.011，土工按 QYBH 精确）+ 闪烁高亮（1.2s×2 次 → 停留 → 自动消退，再点重触发）
- 单孔查询走 `wsReviewHoleQuery(zk)`（带 project_type/max_plasticity/use_std_stratum/include_test）

### 4.3 修正与成果（tools）
- 标贯批量修正：`toolsSpt` 扫描 → 列表显示「原始击数 X → 修改后击数 Y（双击可改）」→ `toolsSptApply` 一键写回；可塑性条目（R-DEN-007）显示「击数 N=X（无需修正）」
- 岩溶统计：**按项目类型动态 1 卡**（A 类→karst_a / B 类→karst_b）

### 4.4 参数中心
- 16 段卡片瀑布流（columns:2，零留白）、段色点（公用蓝/A紫/B绿）、计数徽标
- 字段卡片横排（label 左/控件右 中心对齐）、用途/说明**只读蓝底跨整行**、判定表（cfg-state-table）行式
- 保存：saveConfig 补丁式写 TOML（自动 .bak），disabled 元素跳过

### 4.5 生成 MDB
- 触发生成 → GitHub Actions 转换 → 下载 .lz 产物 / work.db（v32 已删教学性文字，保留状态横幅/任务卡/日志）

### 4.6 全局
- sidebar 可折叠（56px/hover/click/localStorage）；深色模式；Cmd+K / Esc 快捷键；登录 admin/admin

## 5. 关键机制（改代码前必读）

| 机制 | 说明 |
|---|---|
| **单孔主控 WS.zkbh** | 所有页签 + 复核问题面板联动主控；wsSetTab 强制 `keyword=WS.zkbh&search_col=ZKBH&exact=true`；统计卡保持库级概览 |
| **伪表 wsRealTable(t)** | 解析 `__` 前缀 → 真实表名；loadOnlinePage/inlineEdit/cyChange/增删改 6 处 API URL 全部经它（当前仅剩颗分真表 z_c_KeFen，伪表机制保留） |
| **DEPTH_COL** | 各表深度排序列（地层 TCCDSD/土工 QYSD/标贯 BGDSD/动探 DTDSD/水位 SWSD），所有页签默认按 ZKBH+深度排序 |
| **RULE_TAB** | 规则 ID → 页签映射：标贯 R-DEN-001/007/008、R-CRS-012、**R-SPT-001**；动探 R-DEN-002；土工 R-PLS-001/002、R-CRS-002/003、R-GRS-001；其余地层 |
| **jumpToIssue** | 问题卡片定位：`await loadOnlinePage` 后再找行（否则旧行 flash 被重建 DOM 清掉）+ 轮询 2.5s + scrollIntoView(smooth,center) + flash 动画 |
| **WS_SETTINGS** | 复核设置（project_type A/B、max_plasticity、use_std_stratum、include_test），localStorage 持久化，全站共用 |
| **COL_WIDTH / WS_COLS / COLUMN_CN** | 列宽表 / 每页签业务列 / 中文映射（35+ 列全覆盖）；列设置优先用 WS_COLS 过滤 |
| **颗分数据位置** | 颗分在 **z_c_KeFen**（KL 筛孔列），**不在** z_c_QuYang 的 QYZY 列（源库空壳）；带 `_` 后缀列=“是否有该试验”标志位 |

## 6. 验证体系

| 脚本 | 内容 | 说明 |
|---|---|---|
| `work/e2e_ui5.mjs` | 交互增强 26 项 | 已同步 v2 冻结开关断言（默认不冻结+开关开启冻结）、v3 CY 下拉断言 |
| `work/e2e_ui4.mjs` | 排版专项 | 已同步 v5 土工核心列、v16 颗分 z_c_KeFen |
| `tests/e2e_ui.mjs` | UI 基线 12 项 | 未动 |
| `tests/e2e_api.py` | API 回归 16 项 | 未动 |

运行：`node work/e2e_uiX.mjs` / `node tests/e2e_ui.mjs` / `python -m pytest tests/e2e_api.py -q`
**注意**：v18 后 E2E 断言后端 `_issue_to_dict` 新增了 `layer_index/ref_value` 字段（纯新增，不破坏既有断言）；v24 新增 R-SPT-001 规则（测试库无跨层数据 → 0 条）。

## 7. 已知陷阱（改代码前必读）

1. **启动环境**：托管裸解释器 `versions\3.13.12\python.exe` **没有 uvicorn**，必须用 `binaries\python\envs\default\Scripts\python.exe`（uvicorn 0.52.1 + fastapi 0.141.1）。
2. **inlineEdit 拼参**：`onclick="inlineEdit(this, ' + idJs + ', \'' + c + '\')"` 用**单引号包裹列名**，不要改回 `JSON.stringify(c)`（曾致 HTML 双引号断裂）。
3. **loadOnlinePage 覆盖 keyword**：会用 `#onlineSearch` 值覆盖 `OData.keyword`——恢复单孔必须**同步设 `si.value = WS.zkbh`**（v12 踩过）。
4. **flex:1 子项无视父容器 justify-content**：居中必须在子项自身做（`.col-sort` justify-content:center，v22）。
5. **下载鉴权**：`<a href download>` 直链会 401，必须走 `downloadApiFile`/`dlStats`/`dlDxf`（fetch+blob 带 Bearer）。
6. **数据表头断言用 innerText**：表头含隐藏图标（⇅/⌕），`textContent` 含图标字符致断言失败。
7. **颗分/干密度空值**：测试库 z_c_QuYang 的 QYZXMD/QYSTXS/QYZY* 源库为空（0/616）是**正常现象**（真实数据在 z_c_KeFen / 其他列）；显示"—"非 bug。
8. **参数中心 TOML**：TomlFile 补丁模式，`参数/工程配置.toml` 不要重写整个文件（保存自动 .bak）。
9. **列名去重**：TCMC/TCYMC 同义列只显示 TCMC（去重逻辑在 renderOnlineRows 内，勿删）。
10. **grid 模板单位**：`minmax(52px,1fr)` 拼字符串时别漏写/重复 `px`（v6 出现过 `pxpx` 使整个 grid 失效）。
11. **CSS columns 瀑布流**：段卡必须 `break-inside:avoid`；响应式 @media 720px 切 `columns:1`。
12. **jumpToIssue 时序**：必须先 `await loadOnlinePage` 再查行，否则旧 DOM 的 flash 被重建清掉（v18 修复）。
13. **push 重试**：GitHub 443 不稳定，用循环重试（`1..10 | ForEach-Object { git push origin main; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 15 }`）。

## 8. 核心文件地图

| 文件 | 说明 |
|---|---|
| `index.html` | 前端主体（2749 行）：路由/登录/数据工作台/复核/参数中心/工具页/生成 MDB；**所有 UI 改动集中在此** |
| `backend/main.py` | FastAPI 入口：鉴权/上传/表 CRUD（排序白名单）/复核/统计/DXF |
| `backend/db.py` | SQLite 工作库：get_rows（分页/搜索/排序白名单）/upsert/delete/import_mdb（原样拷贝无映射） |
| `backend/review_api.py` | 复核/统计/参数中心 API；`_issue_to_dict` 已透传 layer_index/ref_value（v18） |
| `backend/review/rule_engine.py` | 复核规则引擎：review_strata 先按深度排序后遍历（layer_index 为排序后索引）；含 R-SPT-001 跨层规则（v24） |
| `backend/review/spt_corrector.py` | 标贯修正：scan_all 已激活 compute_suggestions + compute_clay_corrections 分类处理（v23/v24） |
| `backend/auth.py` | Bearer Token 鉴权 |
| `参数/工程配置.toml` | 桌面版基线配置（只读口径，补丁式修改；含 试验指标_统计.N 范围供标贯钳位） |
| `start.bat` | 一键启动（envs/default + 8766 + 自动开浏览器） |
| `work/dbs/e2e_real.lz` | 测试库（408 孔） |
| `work/` | 迭代/E2E/诊断脚本（e2e_ui4/5.mjs 建议保留） |
| `.workbuddy/` | 截图/脚本/备份（backup_index_v30.html 为 v31 清理前备份）/memory 日志（已 gitignore） |

## 9. 当前未提交改动（重要！）

`git status` 显示 5 个文件**未提交**，本次交接前应 commit + push：

| 文件 | 改动 |
|---|---|
| `index.html` | v1~v32 全部前端改动（UI/交互/单孔联动/问题跳转/参数中心等） |
| `backend/review_api.py` | `_issue_to_dict` 透传 layer_index/ref_value（v18） |
| `backend/review/rule_engine.py` | R-SPT-001 跨层标贯规则（v24） |
| `backend/review/spt_corrector.py` | scan_all 激活 compute_suggestions + 分类处理（v23/v24） |
| `start.bat` | 环境修正（envs/default + 8766）+ 自动开浏览器 |

提交建议：`git add -A && git commit -m "feat: 数据工作台单孔联动/问题卡片跳转高亮/标贯修正激活/颗分映射修复/参数中心设计系统 + UI 全面优化（v1-v32）"`，再按陷阱 13 循环 push。

## 10. 待办 / 建议

1. **提交并推送**当前 5 个未提交文件（§9）。
2. **跑全套 E2E** 确认 v1~v32 无回归：`node work/e2e_ui5.mjs && node work/e2e_ui4.mjs && node tests/e2e_ui.mjs && python -m pytest tests/e2e_api.py -q`。
3. **真实项目库验证**：上传真实 .lz/.mdb，确认颗分（z_c_KeFen）与干密度/孔隙比（源库若有值）正常显示；若真实库颗分仍空，才需查 mdb_reader 列映射。
4. 可塑性条目（R-DEN-007）的**状态标注修正写回**（old_state→expected_state）暂无前端路径（v23 遗留，toolsSptApply 只更新 N 值）。
5. 地层列设置里"岩土名称"出现 2 次（TCYMC+TCMC 同义映射）——可改 COLUMN_CN 区分（v20 遗留）。
6. `renderReview/renderBorehole/renderSpt` 已删除（v31）——若未来需要独立复核/标贯页，需基于 renderTools/renderWorkspace 重建。

---
*交接完毕：v1~v32 改动已归档；5 个文件待提交；本地服务已运行最新代码。*
