# /materials UX — Phase 2 决策矩阵 + Phase 4 回滚预案 roll-up

**Issue:** NFM-3933 (child of NFM-3913, owned by CPO)
**Author:** CPO
**Branch:** `NFM-3933-cpo-materials-ux-phase-2-4-roll-up`
**Status:** Phase 1 实证证据已交付 (NFM-3914, commit `20c94eb4`);Phase 3 子票拆分已落地 (NFM-3915/3916/3917/3918/3919)。本文档汇总 **Phase 2 决策矩阵 (D1–D5)** 与 **Phase 4 回滚预案**。

---

## 0. 角色与边界

- **CPO:** 出决策、出回滚预案、出验收。不写代码、不跑 SQL。
- **LE:** 按子票实施。Phase 1 已独立验证根因 (commit `20c94eb4`,585 行证据)。
- **CTO:** 评审 ADR 文档时的技术约束提供方。
- **CEO:** Phase 2 D 系列决策的最终裁决者 (本文档目标读者)。

---

## 1. Phase 2 — D1~D5 决策矩阵

### 1.0 决策机制

父票 NFM-3903 由 Hermes 给出初始推荐 (D1=B / D2=B / D3=C / D4=A / D5=C)。Phase 1 实测证据 (NFM-3914, commit `20c94eb4`) 在以下三项上**推翻**了父票推荐,在以下两项上**确认**了父票推荐。所有翻案均有 6 个独立 SQL / 代码审计 / 启发式跑分证据支撑,见 `docs/research/materials-ux-phase1-evidence.md`。

### 1.1 决策矩阵总表

| ID | 决策点 | 父票推荐 | **Phase 1 决议** | 状态 | 子票 | 翻案依据 |
|----|--------|----------|------------------|------|------|----------|
| **D1** | 前端 dropdown 范围 | B (category + is_active) | **A (仅 category)** | ✅ 翻案 | NFM-3917 Tier 1D | is_active cardinality=1 (131/131 全 true),crystal_structure 覆盖率 2.3% → 父票 B/C 均为负资产 UX |
| **D2** | /materials 默认排序 | B (name asc) | **B (name asc)** | ✅ 确认 | NFM-3915 Tier 1A | created_at desc → 20/20 首屏 Unknown;实测 131/131 is_active=true → D2=C 无效 |
| **D3** | Unknown Material 数据清理 | C (统一归并) | **A+C 混合 (17 硬删 + 10 归并)** | ✅ 翻案 | NFM-3918 Tier 2 | 27 行非同质:17 行零下游数据可硬删,10 行携带 93 measurement 必须归并 (其中 83 集中在单行) |
| **D4** | material_categories seed | A (立即 seed) | **A (seed + backfill,缺一不可)** | 🔺 范围扩展 | NFM-3916 Tier 1C | 父票低估:131/131 行 category_id NULL → 仅 seed 不 backfill 等于无效 |
| **D5** | 阻断新增 Unknown Material | C (双管齐下) | **C (heuristic 上游 + mapper 下游双层守卫)** | ✅ 确认 | NFM-3919 Tier 1B | 上游堵源头 (heuristic 补 material_name/composition) + 下游守底线 (mapper 拒收双缺失 item) |

### 1.2 每个决策的展开

#### D1 — 前端 dropdown 范围 → **A (仅 category)** ⭐ 推翻父票

**Phase 1 实测证据 (NFM-3914 P1.4):**

| 候选维度 | 可选值基数 | 覆盖率 | 加 dropdown 的实际效果 |
|---|---|---|---|
| `category` | 0 (表为空) | 0/131 | Tier 1C 完成后可用 |
| `is_active` | **1** (全 true) | 131/131 | 选"活跃"=全集,选"停用"=空集 → **负资产 UX** |
| `crystal_structure` | 3 | **2.3%** (3/131) | 97.7% 的行被任何选择过滤掉 → 列表页瞬间归零 |

**结论:** 父票 D1=B 推荐的"is_active dropdown"在当前数据状态下是 0 价值 + 负体验。父票 D1=C 加 crystal_structure 更糟(覆盖率 2.3%)。**只做 category 一个 dropdown,弃 is_active 与 crystal_structure。**

**实施入口:** NFM-3917 Tier 1D (blocked-by NFM-3916 Tier 1C)。dropdown 上线前提:Tier 1C 报告 `category_id` 覆盖率 ≥ 50%,否则本票 @CPO 重评是否值得做。

**回滚判据:** 上线后任意一类目下结果集为空且非预期 / 切换类目时分页或 `total` 计数不刷新 / 与搜索框组合行为违反声明。

---

#### D2 — /materials 默认排序 → **B (name asc)** ✅ 确认父票

**Phase 1 实测证据 (NFM-3914 P1.1):**

```sql
SELECT count(*) FROM materials WHERE name='Unknown Material';  -- 27
-- 20/20 集中在 2026-08-31 11:00 UTC 一小时 (ingest burst signature)
```

**实测证据证伪 D2=C:**
```sql
SELECT count(*) FILTER (WHERE is_active=true),
       count(*) FILTER (WHERE is_active=false)
FROM materials;
--  131 | 0   ←── is_active 全部 true,加进 ORDER BY 是无效列
```

**确认 D2=B:**
- 改 `name asc` 后 `"Unknown Material"` 排到 U 段,首屏变成 `Ag-Pt / Au / Au-Pt / CuAu / Cr-Mo / …` 等真实材料。
- 后端已支持 `sort ∈ {name, created_at, updated_at}` 与 `order ∈ {asc, desc}`,**无后端改动**。
- 单文件:`apps/web/src/app/materials/MaterialsListView.tsx`,`fetchMaterials` 拼 `&sort=name&order=asc`。

**实施入口:** NFM-3915 Tier 1A (**已 done**)。本系列唯一零依赖、可立即上线的改动。

**回滚判据 (见 §2.1 Tier 1A 详细预案):**
- 触发回滚:首屏白屏 / 500 / 分页或搜索失效 / `total` 与 `select count(*) from materials` 不符。
- **不触发回滚:** 仅"排序结果不合预期" → 改前端查询参数即可热调。

---

#### D3 — Unknown Material 数据清理 → **A+C 混合 (17 硬删 + 10 归并)** ⭐ 推翻父票

**Phase 1 实测证据 (NFM-3914 + NFM-3918 SQL):**

```sql
select count(*) n_meas, count(distinct d.material_id) n_mat
from property_measurements pm
  join datasets d on d.id=pm.dataset_id
  join materials m on m.id=d.material_id
where m.name='Unknown Material';
--  n_meas=93 | n_mat=10
```

| 分片 | 行数 | 携带 measurement | 处置 |
|---|---|---|---|
| 零下游数据 | **17** | 0 | **A: 硬删除** — 无 measurement / alias / composition,无审计价值 |
| 携带数据 | **10** | 93 (其中 83 集中在单行 `78f74516-ff64-4a26-aa4f-221bd4e33df0`) | **C: 归并重建** — 按 source 回溯 paper,重建正确 Material 身份 |

**弃软删除 (D3=B) 的理由:**
1. `is_active` 目前全库全为 true,是**未被使用**的状态位。把它当墓碑会让该字段语义从"业务状态"滑向"删除标记"。
2. 列表页默认不过滤 `is_active` → 软删除后垃圾行**照样显示在首屏**,等于没删。

**数据坏损证据 (为什么必须归并而非放任):**
```
pre_exponential_factor | 0.00  | 17 次 | 1 个材料
density                | 10.55 |  8 次 | 8 个不同材料  ← 同一物性点被切成 8 份
activation_energy      | 0.30  |  4 次 | 4 个不同材料
```
来源分布证伪父票"几乎全部同一 source"假设:
```
Unattributed source (no DOI)                                         | 11
Owen et al. - 2023 - Diffusion in undoped and Cr-doped amorphous UO2 | 10
9320cb50-eb65-4178-8d2e-c56aeb848b21 (裸 UUID 当标题)                |  2
Unknown Source                                                       |  2
```
最大单一来源仅占 10/27 — **通用 ingest 路径的系统性缺陷**,不是单篇论文偶发。

**实施入口:** NFM-3918 Tier 2 (**blocked-by NFM-3919 Tier 1B**)。staging 干跑优先,产出 before/after 计数对比;归并前必须验证 `uq_pm_dedup UNIQUE (dataset_id, property_type_id, conditions_hash, method)` 在归并后不冲突 — 这是该约束会首次真正生效的时刻。

**回滚判据 / 不可靠 git revert (见 §2.5 Tier 2 详细预案):**
- 本票是系列中**唯一不可靠 `git revert` 回滚**的票,因涉及删除与跨表迁移,**`pg_dump` 备份是唯一回滚路径**,不可省略。

---

#### D4 — material_categories seed → **A (seed + backfill,缺一不可)** 🔺 范围扩展

**Phase 1 实测证据 (NFM-3914 P1.2 + P1.4):**

```sql
SELECT count(*) FROM material_categories;                  -- 0
SELECT count(*) FILTER (WHERE category_id IS NULL)
FROM materials;                                            -- 131
```

migration `009_create_phase1_core_tables.py:114-127` 只 CREATE table 不 INSERT — service/cli/api 全仓 grep `material_categories` 零命中,**既无 seed CLI 也无 startup hook**。

**范围扩展理由:**
父票低估本项成本:seed 类别只是**必要条件**,不是充分条件。131 行 `category_id` 全空,还需要一次 backfill,dropdown 才能真正筛出东西。**两步都在本票范围内,缺一不可。**

**6~10 类推荐起点 (与现有 Universe 100% 兼容):**

| ID | 类别 | 行数 (实测聚类) |
|---|---|---|
| `oxide_fuels` | UO2 + PuO2 + MOX + Cr-doped UO2 + amorphous UO2 + Cr2O3 | 14 |
| `metal_fuels_alloys` | U-Pu-Zr + U-Mo + U-Si + UPu/UZr/PuZr reference phases | 50 |
| `cladding_structural` | Zircaloy + ZIRLO + M5 + ZrNb + α/β Zr | 10 |
| `intermetallic_refractory` | CuAu, Au-Pt, Ag-Pt, Cu3Au, Cr-Nb, Nb-V, Pt-W, Cr-Mo-V, HEA | 13 |
| `fission_products` | Xe, He, H2, Ar, N2 + 纯元素 Cu/Au/U | 8 |
| `coolants_process_fluids` | Steam/H2O | 1 |
| `test_placeholders` | Test, E2E-Test-* (administrative-only,不进 frontend filter) | 2 |
| `amorphous_specialty` | amorphous UO2 (单列,crystalline 划分不合适) | 1 |

**实施入口:** NFM-3916 Tier 1C。匹配不上的行保持 `category_id=NULL` (不硬塞进 `other`,避免制造假分类)。backfill 脚本必须幂等可重跑。

**回滚判据 (见 §2.3 Tier 1C 详细预案):**
- 逆操作为 `update materials set category_id=NULL where category_id in (select id from material_categories)`。因 backfill 前该列全为 NULL (实测),该逆操作**无损**。

---

#### D5 — 阻断新增 Unknown Material → **C (heuristic 上游 + mapper 下游双层守卫)** ✅ 确认父票

**Phase 1 实测证据 (NFM-3914 P1.3 + P1.5 + P1.6):**

根因链:
1. `heuristic_extractor.py:483,524` 只输出 deprecated `element_system` (如 `"UO2"`),**不填 `material_name`,不填 `composition`**。
2. `extraction_to_db_mapper.py:610-611` 兜底字面量 `"Unknown Material"` + formula=None。
3. `_find_material_by_formula(formula=None)` (`:790-791`) 短路返回 None → 跨 run dedup 永不生效,每次 ingest 新建一行。
4. `_material_key` (`:225-230`) 在两字段皆空时恒返回 `"formula:|name:"`,同 run 内坍缩成一行 — 这解释了"每次 run 恰好 +1 行"、27 行 ≈ 27 次 run。
5. 连带:`uq_pm_dedup UNIQUE (dataset_id, property_type_id, conditions_hash, method)` 因每次新建 material→新建 dataset,`dataset_id` 每次不同,**唯一约束跨 run 永不触发**。

**D5=C 双管齐下 — 两条都要做:**

### 上游 (源头) — `heuristic_extractor.py`
`:483` 与 `:524` 附近两处 `found.append({...})` 补上:
```python
"material_name": material,   # 当前的 element_system 值
"composition": material,     # 使 formula 拿到值,_find_material_by_formula 复活
```
保留 `element_system` 不动以免破坏既有消费方。

### 下游 (底线) — `extraction_to_db_mapper.py`
`item.material_name` 与 `item.composition` **双缺失**时,拒收该 item,计入 `skipped_*` 计数并 log warning,**不再 fallback 成 `"Unknown Material"`**。即使未来其他 extractor 也漏填,数据库仍不被污染。

### 顺手修 (本次审计新发现的定时炸弹)
`_find_material_by_formula` 用 `scalar_one_or_none()`,目前全库无重复 formula 所以未爆;一旦出现两行同 formula,该调用抛 `MultipleResultsFound`,**整个 ingest 批次失败**。改为 `.limit(1)` + `.scalars().first()`。

**实施入口:** NFM-3919 Tier 1B (**已 done**)。验收标准含 staging 跑同一 PDF 两次,`select count(*) from materials where name='Unknown Material'` 不增长。

**回滚判据 (见 §2.2 Tier 1B 详细预案):**
- 纯后端单 commit `git revert <sha>`,无 DB migration、无 schema 变更、无状态残留。回滚后行为退回当前(继续产 Unknown),不会损坏已有数据。

---

## 2. Phase 4 — 回滚预案

### 2.0 通用前置

- 所有 prod 部署走 `docker compose -f docker-compose.prod.yml up -d --build web` (或 `api`)。
- 所有回滚后必须 `curl -s localhost:3000/materials | grep -c 'Unknown Material'` 与 `curl -s http://localhost:3001/api/v1/materials?limit=1` 双端冒烟。
- 所有回滚窗口期内 Hermes 监控自动告警 `5xx_rate > 1%` / `materials_count_change_per_hour > 100` 任一触发即中断回滚并升级 CEO。
- 所有 prod DB 变更 (Tier 1C / Tier 2) **必须先 staging 干跑**,且 prod 执行前 `pg_dump` 备份相关表。

### 2.1 Tier 1A — `/materials` 默认排序改 name asc (NFM-3915, **done**)

**改动范围:** 单文件 `apps/web/src/app/materials/MaterialsListView.tsx`,`fetchMaterials` 拼 `&sort=name&order=asc`。**无后端改动**。

**回滚步骤 (1 commit revert):**
```bash
git log --oneline --grep='Tier 1A' -n 5
git revert <sha> --no-edit        # merge commit 用 git revert -m 1 <sha>
git push origin main
docker compose -f docker-compose.prod.yml up -d --build web
curl -s localhost:3000/materials | grep -c 'Unknown Material'
```

**回滚判据 (任一触发):**
- 首屏白屏或 500
- 分页/搜索失效
- `total` 与 `select count(*) from materials` 不符

**不触发回滚 (热调即可):**
- 仅"排序结果不合预期" → 改前端查询参数即可,无需 revert

**回滚耗时:** ≤ 5 分钟 (单文件前端改动,无需 DB 重建或服务重启链)。

---

### 2.2 Tier 1B — 阻断新增 Unknown Material (NFM-3919, **done**)

**改动范围:**
- 上游:`heuristic_extractor.py:483,524` 补 `material_name` / `composition` 字段
- 下游:`extraction_to_db_mapper.py` 拒收双缺失 item,计入 `skipped_*`
- 顺手:`_find_material_by_formula` 改 `.limit(1)` + `.scalars().first()` 防 `MultipleResultsFound`

**回滚步骤 (1 commit revert):**
```bash
git log --oneline --grep='Tier 1B' -n 5
git revert <sha> --no-edit
git push origin main
docker compose -f docker-compose.prod.yml up -d --build api
```

**回滚判据 (任一触发):**
- ingest 流程失败率 > 0 (现有 PDF ingestion 任务退出码非 0 或重试 > 3)
- `skipped_*` 计数异常暴增 (单次 ingest `skipped_* > total_extracted_properties * 50%`)
- `_find_material_by_formula` 改 `.limit(1)` 后出现"两行同 formula 但只取一行"造成的隐式数据丢失 → 立即回滚并打 hotfix 票恢复 `scalar_one_or_none` + 数据完整性 audit

**回滚后行为:** 退回当前状态(继续产 Unknown),**不会损坏已有数据**。这是 P0 阻断票的可逆性保证 — rollback 是 always-safe 路径。

**回滚耗时:** ≤ 5 分钟。

---

### 2.3 Tier 1C — material_categories seed + materials.category_id backfill (NFM-3916, blocked)

**改动范围:**
- `alembic` migration: seed 6~10 个 `material_categories` 行
- backfill 脚本:按 `formula` / `name` 规则映射,匹配不上的行保持 NULL
- **无 schema 变更** (列已存在,只填值)

**回滚步骤 (2 步):**
```bash
# 1. backfill 逆操作 (无损,因该列回滚前全为 NULL)
psql -h localhost -p 5433 -U nfm -d nfm_db -c \
  "update materials set category_id=NULL where category_id in (select id from material_categories)"

# 2. alembic downgrade (移除 seed)
alembic downgrade -1
```

**回滚判据 (任一触发):**
- backfill 脚本**非幂等**(连跑两次结果不一致)
- 覆盖率 < 50% (Tier 1D 上线判据,backfill 必须 ≥ 50%)
- backfill 后 `material_categories` 行数 > 10 (命名/聚类口径失控)
- backfill 后任意 `material.formula` 出现 NULL 但 `category_id` 非 NULL 的状态 (分类与组成失配,数据语义错乱)

**回滚耗时:** ≤ 10 分钟 (DB 操作 + alembic downgrade)。

---

### 2.4 Tier 1D — /materials category dropdown (NFM-3917, blocked-by Tier 1C)

**改动范围:**
- 前端 `apps/web/src/app/materials/MaterialsListView.tsx`:加一个 antd `Select`,选中拼 `&category_id=<uuid>`
- 可能新增 `GET /api/v1/material-categories` 只读接口 (若不存在)
- 后端 list 端点已支持 `category_id: UUID | None` 查询参数,**无需改动**

**回滚步骤 (1 commit revert,纯前端或前端 + 纯新增只读接口):**
```bash
git log --oneline --grep='Tier 1D' -n 5
git revert <sha> --no-edit
git push origin main
docker compose -f docker-compose.prod.yml up -d --build web
```

**回滚判据 (任一触发):**
- dropdown 列表为空且非 Tier 1C 故障 (前端 bug)
- 选中类别后列表与 `total` 计数未正确收窄
- `allowClear` 清空后未回到全量
- 切换类别未重置到第 1 页
- 与搜索框组合行为违反票内声明

**回滚耗时:** ≤ 5 分钟。

**前置条件 (硬阻塞):**
- **Tier 1C 覆盖率 < 50% 时不开工本票**,先在本票留言 @CPO 重评。

---

### 2.5 Tier 2 — 清理 27 条 Unknown Material (NFM-3918, blocked-by Tier 1B)

**改动范围:**
1. 硬删除 17 条零下游数据的 Unknown (含其空 dataset 行)
2. 归并 10 条:按 `source_id` 回溯原始 paper,重建正确 Material 身份,把 93 条 measurement 迁移过去
3. 归并前**必须**验证 `uq_pm_dedup UNIQUE (dataset_id, property_type_id, conditions_hash, method)` 在归并后不冲突
4. 迁移脚本幂等可重跑

**回滚步骤 (从 `pg_dump` 恢复 — **唯一回滚路径**,不可省略备份):**
```bash
# 执行前必做 (NFM-3918 验收标准硬要求)
pg_dump -h localhost -p 5433 -U nfm -d nfm_db \
  -t materials -t datasets -t property_measurements \
  -Fc -f /backup/pre-unknown-cleanup-$(date +%Y%m%d-%H%M).dump

# 回滚时
pg_restore -h localhost -p 5433 -U nfm -d nfm_db \
  --clean --if-exists --table=materials --table=datasets --table=property_measurements \
  /backup/pre-unknown-cleanup-*.dump
```

**回滚判据 (任一触发):**
- 归并后 `uq_pm_dedup` 出现冲突行未被脚本处理
- 93 条 measurement 中任一丢失
- `density=10.55` 归并后仍关联到 > 1 个 material
- 出现孤儿 dataset / measurement (外键完整性破坏)
- 软删除路径被错误触发 (本票承诺**只硬删**,绝不写 `is_active=false`)

**为什么不能 git revert:**
本票涉及 `DELETE` SQL + `UPDATE materials SET category_id=...` + 跨表 migration。git revert 只能回滚代码,无法回滚已 commit 的 DB 变更。**`pg_dump` 是唯一回滚路径,不可省略。**

**回滚耗时:** ≤ 30 分钟 (pg_restore + alembic head 校验 + 冒烟测试)。

**额外保险:** 归并前在 staging 干跑,产出 before/after 计数对比表,提交 CPO 审核后才申请 prod 窗口。

---

### 2.6 跨子票回滚顺序 (强制 — 防级联回滚踩踏)

若 Tier 1B → Tier 1C → Tier 1D → Tier 2 中任一票需要回滚,**回滚顺序按部署的逆序**:
1. Tier 2 先回滚 (DB 层) → pg_restore 恢复三表
2. Tier 1D 再回滚 (前端 dropdown)
3. Tier 1C 再回滚 (alembic downgrade -1)
4. Tier 1B 最后回滚 (mapper 守卫解除 — 让 Tier 2 撤下来的 Unknown 仍可被 mapper 兜底)

**禁止顺序:** Tier 1B 先于 Tier 2 回滚 — Tier 2 已删的 Unknown 行再次被 ingest 灌回,Tier 2 回滚等于白做。

**禁止并发回滚:** 任何两票同时回滚 = 数据状态不确定。Hermes 必须在回滚窗口期强制锁定 prod 写入 (`/api/v1/ingest` 临时返回 503)。

---

## 3. 决策矩阵与回滚预案的交叉引用 (CEO 裁决用)

| 决策 | 推荐 | 翻案? | 回滚成本 | 子票 | 阻塞依赖 |
|---|---|---|---|---|---|
| D1 dropdown 范围 | A | ⭐ 推翻父票 B | ≤ 5 分钟 (纯前端) | NFM-3917 | NFM-3916 (Tier 1C ≥ 50%) |
| D2 默认排序 | B | ✅ 确认 | ≤ 5 分钟 (单文件前端) | NFM-3915 ✓ done | 无 |
| D3 Unknown 清理 | A+C 混合 | ⭐ 推翻父票 C | ≤ 30 分钟 (pg_restore) | NFM-3918 | NFM-3919 (Tier 1B) |
| D4 categories seed | A + backfill | 🔺 范围扩展 | ≤ 10 分钟 (DB) | NFM-3916 | 无 |
| D5 阻断新增 Unknown | C 双管齐下 | ✅ 确认 | ≤ 5 分钟 (always-safe) | NFM-3919 ✓ done | 无 |

**翻案总账:** 5 项中 2 项翻案 (D1, D3),1 项范围扩展 (D4),2 项确认 (D2, D5)。**所有翻案均有 NFM-3914 Phase 1 实测证据支撑,无一处基于 Hermes 预排查结论**。

**优先级建议:** Tier 1B (P0, 已 done) → Tier 1A (P1, 已 done) → Tier 1C (P1, blocked) → Tier 1D (P1, blocked-by 1C) → Tier 2 (P2, blocked-by 1B)。

---

## 4. 范围外 (与 Phase 2/4 无关,留待后续 Epic)

- `_material_key` 加 `(source_doi, formula)` 兜底 dedup (NFM-3914 P1.6 open question #1) — Phase 1 仅做影响评估未提交 patch,推荐纳入 Phase 5 follow-up Epic
- Unknown Material 数据清理以外的 legacy data 审计 (e.g. `description` / `crystal_structure` 字段填充)
- `is_active` 字段语义重定义 (当前未被使用,任何后续票应先决定其语义)
- material_categories ontology 与 Universe 全量对齐审计 (Phase 1 仅 6~10 类推荐起点,完整 ontology 待 Nuclear Domain Expert 评审)

---

## 5. 交付清单

| Deliverable | 状态 | 路径 / 引用 |
|---|---|---|
| 本文档 (Phase 2 决策 + Phase 4 回滚) | ✅ 本文件 | `docs/research/materials-ux-phase2-4-rollup.md` |
| Phase 1 实证证据 (585 行) | ✅ | commit `20c94eb4` — `docs/research/materials-ux-phase1-evidence.md` |
| Tier 1A (NFM-3915) | ✅ done | `fix(NFM-3915): /materials 默认排序改 name asc` |
| Tier 1B (NFM-3919) | ✅ done | `fix(NFM-3919): block new Unknown Material rows at the mapper bottom line` |
| Tier 1C (NFM-3916) | ⏸ blocked | seed + backfill 待实施 |
| Tier 1D (NFM-3917) | ⏸ blocked-by 1C | category dropdown 待实施 |
| Tier 2 (NFM-3918) | ⏸ blocked-by 1B | 数据清理待 staging 干跑 |

---

## 6. CEO 裁决点 (本文件的最终读者)

请就以下 3 项裁决后,CPO 解锁 Phase 3 子票实施链:

1. **D1 翻案接受?** (A: 仅 category / B: 维持父票 category + is_active)
2. **D3 翻案接受?** (A+C 混合 / 维持父票 C 统一归并)
3. **D4 范围扩展接受?** (A + backfill / 维持父票 A 仅 seed)

裁决后子票按以下顺序解锁:
- D1 接受 → NFM-3917 Tier 1D 解锁条件:NFM-3916 覆盖率 ≥ 50%
- D3 接受 → NFM-3918 Tier 2 解锁条件:NFM-3919 已 done (✅)
- D4 接受 → NFM-3916 Tier 1C 解锁条件:无 (可立即开工)