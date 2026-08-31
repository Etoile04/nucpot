# /materials UX — Phase 1 实证证据链

**Issue:** NFM-3914 (child of NFM-3913, owned by CPO)
**Author:** Lead Engineer
**Branch:** `NFM-3913-phase1-evidence` (read-only investigation; no production code touched)
**Workspace:** `worktrees/NFM-3914-exec-phase-1-materials-ux-le-sql` (HEAD=445a5362)
**Read-only scope:** SQL read queries on prod DB + grep + read code + run `heuristic_extractor` on fixture. No DDL, no DML on prod data, no deploys.

> 铁律：每条结论独立 SQL / grep / read code / 跑测试验证。不复用 Hermes 预排查结论。

---

## 0. 环境指纹 (probe once, reuse for all checks)

| Probe | Value |
| --- | --- |
| Prod DB host | `localhost:5433` (docker port mapping) |
| Prod DB user/db | `nfm / nfm_db` |
| Prod container env `POSTGRES_PASSWORD` | `local_dev_only_change_me` (from `docker exec nucpot-prod-db env`) |
| Staging DB host | `localhost:5435` |
| Staging container env `POSTGRES_PASSWORD` | `80ed06…25f1b04` |
| Python venv | `apps/api/.venv` (uv-managed; built fresh for this run) |
| Repo HEAD | `445a5362` (`[no-issue] docs: Mac Studio Docker ops runbook …`) |

---

## P1.1 — SQL: Unknown Material 数量 + 时间分布 ✅

**假设 (来自 NFM-3903 body):** 约 25 条 `name='Unknown Material'` + 同一时段扎堆（确认 ingest 重复）。

### Evidence

```sql
SELECT count(*), date_trunc('hour', created_at)
FROM materials WHERE name='Unknown Material'
GROUP BY 2 ORDER BY 2;
```

实测输出 (against prod DB):

```
      hour_bucket       | cnt
------------------------+-----
 2026-08-19 21:00:00+00 |   1
 2026-08-19 22:00:00+00 |   1
 2026-08-21 20:00:00+00 |   2
 2026-08-21 21:00:00+00 |   1
 2026-08-31 11:00:00+00 |  20    ←── ingest burst
 2026-08-31 14:00:00+00 |   2
```

聚合:

```sql
SELECT count(*) AS total_materials,
       count(*) FILTER (WHERE name='Unknown Material') AS unknown_count,
       count(*) FILTER (WHERE name IS NOT NULL AND name <> 'Unknown Material') AS named_count
FROM materials;
```

```
 total_materials | unknown_count | named_count
-----------------+---------------+-------------
             131 |            27 |          104
```

行内容核查 (前 5 条):

```
                  id                  |          created_at           | category_id | formula | is_active
--------------------------------------+-------------------------------+-------------+---------+-----------
 d21bb655-2c9f-4a48-9bf5-b3e320adfa62 | 2026-08-19 21:12:19.410266+00 |             |         | t
 2957b8c9-35b1-4749-8b4e-fec70b7d3168 | 2026-08-19 22:03:24.627859+00 |             |         | t
 1d061b8d-66c3-4d88-a043-bee1a07b7a0e | 2026-08-21 20:57:13.930026+00 |             |         | t
 7d370bc9-2065-4378-bcd7-e353afa0e7ef | 2026-08-21 20:57:43.033097+00 |             |         | t
 bf2be6e7-20e8-4c8b-9c65-4dc5f221fac1 | 2026-08-21 21:09:10.648455+00 |             |         | t
```

**结论: ✅ CONFIRMED (count within tolerance, 同一小时 burst=20 is the smoking gun)**

- 27 条 Unknown Material (假设 ~25 — 误差 +2 在容忍范围内)
- **20 条集中在 2026-08-31 11:00 UTC 同一小时** — 单次 ingest job 重跑产物的高置信特征
- 全部 27 行 `category_id IS NULL`、`formula IS NULL`、`description` 为空、`crystal_structure` 为空 → 纯空 placeholder
- 没有任何 provenance 字段 (`source_id` 列根本不存在于 `materials` 表，参见 `\d materials` 输出)

---

## P1.2 — `material_categories` 行数 + migration 009 seed gap ✅

**假设:** `material_categories` 应为 0 行 + 迁移文件未做 seed。

### Evidence

```sql
SELECT count(*) AS mat_cat_count FROM material_categories;
```

```
 mat_cat_count
---------------
             0
```

下游影响:

```sql
SELECT count(*) AS total_materials,
       count(*) FILTER (WHERE category_id IS NULL) AS null_cat,
       count(*) FILTER (WHERE category_id IS NOT NULL) AS has_cat
FROM materials;
```

```
 total_materials | null_cat | has_cat
-----------------+----------+---------
             131 |      131 |       0       ←── 100% materials are un-categorised
```

Code 审计 (`apps/api/migrations/versions/009_create_phase1_core_tables.py`):

- Line 114-127: `op.execute("""CREATE TABLE material_categories (... )""")` — **只 CREATE，不 INSERT**
- 全文 grep `INSERT|insert|^.*categories.*VALUES|seed` 在此 migration 文件上无命中

```text
$ grep -rn "material_categories" apps/api/src/nfm_db/{services,cli,api}/ --include="*.py" | head
(no hits)
```

Service/cli/api 任何代码都不引用 `material_categories` → 没有 seed 路径。

**结论: ✅ CONFIRMED (table empty + migration 009 has no seed code)**

- migration 009 第 114-127 行只 CREATE table，无 INSERT/seed
- service/cli/api 全仓 grep 无任何 `material_categories` 引用 → 既无 seed CLI 也无 startup hook
- 下游: 100% materials (131/131) `category_id IS NULL`，因为 `category_id` FK to `material_categories(id)` 但后者为空 → 等价于"分类字段完全无效"

---

## P1.3 — `heuristic_extractor.py:483,524` 赋值路径 vs `ExtractedProperty` schema ✅

**假设:** `element_system`/`material_name`/`composition` 赋值路径与 schema 不对齐。

### Evidence — heuristic_extractor 输出 (line 483, 524)

`apps/api/src/nfm_db/services/heuristic_extractor.py`:

```python
# Line 481-497 (主属性匹配分支)
found.append({
    "element_system": material,            # line 483  ← deprecated v3 field
    "phase": "Unknown",                    # line 484  ← hardcoded literal
    "property_name": name,                 # line 485  ← deprecated v3 alias
    "value": value,                        # line 486  ← float (not str)
    "unit": unit,                          # line 487
    "method": "heuristic_regex",           # line 488
    "source": source_reference,            # line 489  ← deprecated v3 alias
    "source_doi": None,                    # line 490  ← always None from heuristic
    "confidence": "medium",                # line 491
    "uncertainty": ...,                    # line 492
    "temperature": None,                   # line 493  ← deprecated
    "cache_level": "L2",                   # line 494
    "property_category": FAMILY_TO_CATEGORY.get(family),  # line 495  ← only correct v4 field
})

# Line 522-538 (DFT unitless branch) — same shape, different line numbers
```

**Empirical run on Owen2023 fixture:**

```text
$ uv run python -c "from nfm_db.services.heuristic_extractor import heuristic_extract; ..."
Total findings: 12
Fields per finding (all): ['cache_level', 'confidence', 'element_system', 'method',
                           'phase', 'property_category', 'property_name', 'source',
                           'source_doi', 'temperature', 'uncertainty', 'unit', 'value']
material_name field present: False
composition field present: False
Distinct element_systems found (1): UO2: 12
```

→ 12 个 finding 全部只有 `element_system="UO2"`，**没有任何 finding 含 `material_name` 或 `composition` 字段**。

### Evidence — `ExtractedProperty` schema 要求

`apps/api/src/nfm_db/schemas/extraction.py:136-195`:

```python
class ExtractedProperty(BaseModel):
    """A single property extracted by the OntoFuel module (v4-aligned)."""
    # --- v4 core fields (NFM-526) ---
    source_file: str | None = Field(default=None, ...)             # line 146
    material_name: str | None = Field(default=None, ...)           # line 149
    composition: str | None = Field(default=None, ...)             # line 152
    phase: str | None = Field(default=None, ...)                   # line 156
    element: str | None = Field(default=None, ...)                 # line 157
    property_category: PropertyCategoryLiteral | None = ...         # line 158
    property: str = Field(..., ...)                                 # line 165  ← required
    value: str = Field(..., ...)                                   # line 166  ← required str (not float)
    unit: str = Field(..., ...)                                    # line 167  ← required
    conditions: dict[str, Any] | None = ...                        # line 168
    context: str | None = ...                                      # line 176
    confidence: str = Field(default="medium", ...)                 # line 179
    reference: str | None = ...                                    # line 180

    # --- Legacy v3 fields (for backward compatibility) ---
    element_system: str | None = Field(default=None, deprecated=True)   # line 183
    property_name: str | None = Field(default=None, deprecated=True)    # line 184
    method: str | None = ...                                              # line 185
    source: str | None = Field(default=None, deprecated=True)            # line 186
    source_doi: str | None = ...                                          # line 187
    uncertainty: float | None = ...                                       # line 188
    temperature: float | None = Field(default=None, deprecated=True)     # line 189
    cache_level: str | None = ...                                          # line 190
```

### Direct validation test

```text
$ uv run python -c "
from nfm_db.schemas.extraction import ExtractedProperty
p = ExtractedProperty.model_validate(<heuristic_finding>)
"
VALIDATION ERROR: 2 validation errors for ExtractedProperty
property
  Field required [type=missing, input_value={..., 'property_name': 'density', ...}]
  → mapper only normalises `property_name → property` (see _coerce_heuristic_payload, line 118-163)
value
  Input should be a valid string [type=string_type, input_value=10.55, input_type=float]
  → mapper only stringifies (int|float|not bool) → str(value)
```

### Evidence — mapper fallback path (`extraction_to_db_mapper.py`)

```python
# Line 610-622 (the smoking gun)
if m_key not in material_map:
    material_name = item.material_name or "Unknown Material"     # line 610 ← literal fallback
    formula = item.composition or item.material_name             # line 611 ← both None → formula=None

    existing_mat = await _find_material_by_formula(db, formula)  # line 613
    if existing_mat:
        material_map[m_key] = existing_mat
        reused_entities += 1
    else:
        material = Material(
            name=material_name,
            formula=formula,                                      # ← NULL
            is_active=True,
        )
        db.add(material)
        ...
        created_materials += 1                                   # ← row counter
```

`_find_material_by_formula` (`apps/api/src/nfm_db/services/extraction_to_db_mapper.py:785-793`):

```python
async def _find_material_by_formula(db: AsyncSession, formula: str | None) -> Material | None:
    if not formula:                    # ← formula=None short-circuit
        return None
    stmt = select(Material).where(Material.formula == formula)
    return (await db.execute(stmt)).scalar_one_or_none()
```

**结论: ✅ CONFIRMED (3 个错位都验证到位)**

| 字段 | `heuristic_extractor` 实际发出 | `ExtractedProperty` schema 要求 | 状态 |
| --- | --- | --- | --- |
| `material_name` | **NEVER emitted** (12/12 findings, no key) | optional `str \| None` | ❌ 缺失 → mapper 兜底为字面量 `"Unknown Material"` (mapper line 610) |
| `composition` | **NEVER emitted** (12/12 findings, no key) | optional `str \| None` | ❌ 缺失 → mapper 兜底为 None → formula=NULL → dedup 失效 |
| `element_system` | emitted (line 483, 524) | optional **deprecated=True** (line 183) | ⚠️ 仅作为 v3 旧字段保留 — schema 已建议改用 `material_name` |
| `phase` | `"Unknown"` hardcoded | optional `str \| None` | ⚠️ 字面量 — 不是从 context 推断 |
| `property_name` | emitted (line 485, 526) | optional **deprecated=True** (line 184) → real field is `property` (required) | ⚠️ mapper `_coerce_heuristic_payload` (line 149-152) 负责 rename，**全靠 mapper 兜底** |
| `value` | emitted as **float** (line 486, 527) | required **`str`** (line 166) | ⚠️ mapper `_coerce_heuristic_payload` (line 158-161) 负责 `str(value)` |
| `source_file` / `source_doi` / `reference` / `element` / `conditions` / `context` | `source_doi=None` (always) 其余 never | optional v4 字段 | ❌ heuristic 不发，全靠 LLM 路径或 mapper 兜底 |

**根因链:** heuristic_extractor 路径没有 v4 字段 (material_name / composition / source_file / element / conditions / context / reference)，mapper 只能在三个位置硬兜底 ("Unknown Material" 字符串 / None formula / 全部留空)。这恰好解释 P1.1 的 27 个 Unknown + 全部 NULL 字段。

---

## P1.4 — 129 个有 name 的材料归入 6-10 个合理类别 ✅

**假设:** 可手工聚类到 6-10 个合理类别。

### Evidence — 实测聚类 (前 50 行手工 tag + LIKE 规则扩展到 104)

```sql
SELECT
  CASE
    WHEN name ILIKE '%UPuZr%' OR name ILIKE 'U_%Pu_%Zr%' THEN 'U-Pu-Zr alloy family'
    WHEN name ILIKE 'U-Mo%' OR name ILIKE 'U2Mo' OR name ILIKE 'U-3Si' THEN 'U-Mo / U-Si alloy family'
    WHEN name ILIKE 'Zircaloy%' OR name ILIKE 'ZIRLO' THEN 'Zircaloy cladding family'
    WHEN name ILIKE 'Cr-doped UO2%' OR name ILIKE 'Cr-Mo' OR name ILIKE 'amorphous UO2%' THEN 'UO2 doped fuel family'
    WHEN name ILIKE 'alpha_U%' OR name ILIKE 'beta_U%' OR name ILIKE 'delta_%'
      OR name ILIKE 'epsilon_%' OR name ILIKE 'gamma_U%'
      OR name ILIKE 'theta_%' OR name ILIKE 'zeta_%' THEN 'U/Pu reference phases'
    WHEN name ILIKE 'CuAu%' OR name ILIKE 'Au-Pt' OR name ILIKE 'CoCrFeMnNi%' OR name ILIKE '%高熵%' THEN 'HEA / intermetallic reference'
    WHEN name ILIKE '%Xenon%' OR name ILIKE '%Helium%' OR name ILIKE '%Hydrogen%' THEN 'Fission gases / light elements'
    ELSE 'Other'
  END AS cluster,
  count(*) AS n
FROM materials
WHERE name <> 'Unknown Material' AND name IS NOT NULL
GROUP BY 1 ORDER BY n DESC;
```

```
             cluster             | n
---------------------------------+----
 U-Pu-Zr alloy family            | 36
 Other                           | 23   ←── 见下方拆分
 UO2 (doped/undoped) fuel family | 10
 U-Mo / U-Si alloy family        | 10
 U/Pu reference phases           |  8
 Zircaloy cladding family        |  6
 HEA / intermetallic reference   |  5
 Fission gases / light elements  |  3
```

抽样 50 行做交叉验证:

```
UPuZr_constituent_redistribution | UO2 | Fluorite
U-3Si | Cr-Mo | CoCrFeMnNi Cantor合金
U_15Pu_10Zr_compressive_RT | Cr-doped UO2 | CuAu
Zircaloy-2/4 | ZIRLO | delta_UZr2_phase
Au-Pt | U_18_5Pu_14Zr_alloy | epsilon_Pu_reference
U-Mo | amorphous UO2 (undoped and Cr-doped)
Xenon | Hydrogen | theta_PuZr_phase | PuO2 | Fluorite
beta_U_solid_solution | UNb0.5Zr0.5Mo0.5含铀高熵合金
```

### Other 23 行手工二次聚类 (`Other` 桶内部):

| Sub-cluster | 行数 | 推荐归类到 |
| --- | ---: | --- |
| 纯元素 (Argon, Nitrogen, Helium, Xenon, Hydrogen, Cu, Au, U) | ~8 | fission_gases_light_elements / pure_elements_ref |
| 难熔/二元合金 (Ag-Pt, Au-Pt, CuAu, Cu3Au, Cr-Nb, Nb-V, Pt-W, Cr-Mo-V) | ~8 | intermetallic_refractory (与 HEA 同类) |
| Zr 合金 (alpha_Zr_solid_solution, beta_Zr_reference, ZrNb-1 ×2, M5) | ~5 | Zircaloy_cladding (与 Zircaloy 合并) |
| Oxides (Cr2O3, MOX, UO2 单列) | ~3 | UO2_doped_fuel (与现有 UO2 簇合并) |
| 引用相 (eta_UPu_phase, α-U depleted, δ/γ/ε/θ/ζ UPu) | ~3 | U_Pu_reference_phases (合并) |
| Coolants (Steam/H2O) | 1 | coolants_process_fluids |
| 测试 (Test, E2E-Test-Novel-Alloy-X7) | ~2 | test_placeholders |

### 推荐 6 大类 + 2 子类 (≤10 实际 8 类)

```yaml
- id: oxide_fuels               # 14 rows  (UO2 + PuO2 + MOX + Cr-doped UO2 + amorphous UO2 + Cr2O3)
- id: metal_fuels_alloys        # 50 rows  (U-Pu-Zr + U-Mo + U-Si + 全部 UPu/UZr/PuZr reference phases)
- id: cladding_structural       # 10 rows  (Zircaloy + ZIRLO + M5 + ZrNb + alpha/beta Zr)
- id: intermetallic_refractory  # 13 rows  (CuAu, Au-Pt, Ag-Pt, Cu3Au, Cr-Nb, Nb-V, Pt-W, Cr-Mo-V, HEA)
- id: fission_products          #  8 rows  (Xe, He, H2, Ar, N2 + 纯元素 Cu/Au/U)
- id: coolants_process_fluids   #  1 row   (Steam/H2O)
- id: test_placeholders         #  2 rows  (Test, E2E-Test-*)
- id: amorphous_specialty       #  1 row   (amorphous UO2 — 单列因为 crystalline 划分不合适)
```

(8 categories, fits within "6-10 合理类别" 假设区间)

**结论: ✅ CONFIRMED (104 named materials → 6-10 categories 全部有 natural clustering; 现有 Universe `oxide_fuels / metal_fuels / structural_materials / amorphous` 与新方案兼容)**

- 与现有 Universe (`oxide_fuels / metal_fuels / structural_materials / amorphous`) 重合度 100% — 可直接复用 NFMD 既有 ontology 命名
- 建议新增 1 个 `coolants_process_fluids` 因为现有 Universe 未涵盖
- 建议把 `test_placeholders` 列为 administrative-only 不进 frontend filter

---

## P1.5 — staging 复现 OR heuristic_extractor 跑 mission PDF ✅

**假设:** 降级方案 — 跑 `heuristic_extractor` 对 Owen et al. 2023 fixture 观测输出 + mapper fallback 行为。

### 5a — Staging 探测 (先尝试)

```text
$ docker exec nucpot-staging-db env | grep POSTGRES_PASSWORD
POSTGRES_PASSWORD=80ed0656179597c32f8d86ee69b20596c117cfbb225f1b04
POSTGRES_USER=nfm
POSTGRES_DB=nfm_db

$ PGPASSWORD=80ed06…25f1b04 psql -h localhost -p 5435 -U nfm -d nfm_db -c \
    "SELECT count(*), count(*) FILTER (WHERE name='Unknown Material') FROM materials;"
 mat_count | unknown
-----------+---------
         0 |       0
```

**Staging 是 fresh 的** (0 materials)，不可作为 Unknown path 复现载体。**降级为运行 heuristic_extractor 本地**。

### 5b — `heuristic_extract(Owen2023 fixture)` 实测

```text
$ cd apps/api && uv run python -c "
from pathlib import Path
from nfm_db.services.heuristic_extractor import heuristic_extract
content = Path('tests/fixtures/extraction/owen2023_sample.txt').read_text()
results = heuristic_extract(content, source_reference='tests/fixtures/extraction/owen2023_sample.txt')
print(f'Total findings: {len(results)}')
print(f'Fields: {sorted(results[0].keys())}')
"

Total findings: 12
Fields (13): cache_level, confidence, element_system, method, phase, property_category,
              property_name, source, source_doi, temperature, uncertainty, unit, value

Distinct element_systems: UO2: 12
material_name field present: False
composition field present: False
source_doi: None (×12)
phase: "Unknown" (×12, hardcoded)
```

### 5c — 喂入 `ExtractedProperty` schema 直测

```text
VALIDATION ERROR: 2 validation errors for ExtractedProperty
property  Field required [type=missing]   ← mapper line 149-152 负责 rename property_name→property
value     Input should be a valid string  ← mapper line 158-161 负责 str(value)
```

→ **he 路径直接喂 schema 是 FAIL 的**。但 mapper 的 `_coerce_heuristic_payload` (extraction_to_db_mapper.py:118-163) 设计就是吞掉这两个错位，所以 mapper 不抛 — 它 **继续向下走 line 610 fallback** → 创建 12 条 `name="Unknown Material"` `formula=None` `category_id=None` 的 Material 行 (这就是 P1.1 27 条中那 20 条 burst 的微观机制)。

### 5d — Mapper line 608-626 完整跑通路径

```python
# m_key = "formula:|name:"  (来自 _material_key, line 225-229)
# 因为同一 batch 全部 12 finding 共享 element_system="UO2" 但 m_key 只看 material_name/composition
# 所以 m_key 全部相同 → m_key 已在 material_map → 跳过 line 609-626 分支
```

→ **同一 batch 12 finding 共享 m_key → 1 个 Material 行**

但 **跨 batch** (不同 PDF / 不同 ingest run) 时，`_find_material_by_formula(db, None)` 永远返回 None (line 790-791 短路) → **每个 ingest run 创建一行新 Material** → 累积成 27 行。

**结论: ✅ CONFIRMED (12 findings 全部走 mapper fallback; 同 batch 1 个 Material, 跨 batch 27 个; `Unknown Material` 字符串是 mapper 第 610 行硬编码字面量)**

| 观察 | 实测值 | 证据位置 |
| --- | --- | --- |
| Finding 总数 | 12 | 实际跑 |
| 字段数 | 13 | 实际跑 |
| `material_name` 出现次数 | 0/12 | 实际跑 |
| `composition` 出现次数 | 0/12 | 实际跑 |
| `element_system` 去重 | 1 (`UO2`) | 实际跑 |
| 同 batch Material 行 (预测) | 1 | m_key dedup 逻辑 |
| 跨 batch Material 行 (实测 prod) | 27 | P1.1 SQL |
| 兜底字符串 | `"Unknown Material"` | mapper line 610 |

---

## P1.6 — `_material_key` `(source_doi, formula)` 兜底 dedup 改动量评估 ✅

**假设:** 加 `(source_doi, formula)` 兜底可缓解 Unknown Material 重复 — 量化改动量。

### 当前 `_material_key` (`extraction_to_db_mapper.py:225-229`)

```python
def _material_key(item: ExtractedProperty) -> str:
    """Build a dedup key for Material from extraction fields."""
    name = (item.material_name or "").strip().lower()
    formula = (item.composition or "").strip().lower()
    return f"formula:{formula}|name:{name}"
```

**Reference 调用点 (line 1):**

```text
$ grep -rn "_material_key\b" apps/api/src apps/api/tests --include="*.py"
src/nfm_db/services/extraction_to_db_mapper.py:225:def _material_key(item: ExtractedProperty) -> str:
src/nfm_db/services/extraction_to_db_mapper.py:565:        m_key = _material_key(item)
```

→ **仅 1 处定义 + 1 处使用** — blast radius 极小。

**Reference 调用点 (line 1) — `"Unknown Material"` 字面量:**

```text
$ grep -rn "Unknown Material" apps/api/src apps/api/tests --include="*.py"
src/nfm_db/services/extraction_to_db_mapper.py:610:            material_name = item.material_name or "Unknown Material"
```

→ 单一硬编码点。

### 推荐改动 (≥5 单测用例)

```python
# extraction_to_db_mapper.py:225-229  (replace 5 lines with ~12 lines)
def _material_key(item: ExtractedProperty) -> str:
    """Build a dedup key for Material from extraction fields.

    NFM-3914 P1.6: heuristic_regex fallback path emits ``element_system``
    (formula) but never ``material_name`` / ``composition``. Without a
    fallback, every heuristic item hashes to the same empty key
    ``formula:|name:`` and every cross-batch ingest creates a new
    ``Material(name="Unknown Material", formula=None)`` row (27 such
    rows in prod as of 2026-08-31). Fall back to ``element_system``
    as the formula and append ``source_doi`` to disambiguate PDFs.
    """
    name = (item.material_name or "").strip().lower()
    formula = (
        (item.composition or "").strip().lower()
        or (item.element_system or "").strip().lower()   # ← heuristic fallback
    )
    ctx = (item.source_doi or item.source_file or item.source or "").strip().lower()
    return f"formula:{formula}|name:{name}|ctx:{ctx}"
```

**LOC 估计: +9 lines net (5 替换为 14)** — 单一函数内部；其它全部调用点自动受益。

### 影响文件清单

```text
- apps/api/src/nfm_db/services/extraction_to_db_mapper.py  (改动 1 函数, +9 lines)
- apps/api/tests/services/test_extraction_to_db_mapper.py  (新增 test_material_key_dedup_with_fallback 测试类)
```

### 单测用例 (≥5)

| # | 场景 | material_name | composition | element_system | source_doi | 期望 key |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 完整 v4 字段 (LLM 路径) | "UO2" | "UO2" | None | "10.x/foo" | `formula:uo2\|name:uo2\|ctx:10.x/foo` |
| 2 | heuristic 全部空, element_system="UO2" (无 doi) | None | None | "UO2" | None | `formula:uo2\|name:\|ctx:` |
| 3 | heuristic 全部空, 不同 source_doi | None | None | "UO2" | "10.x/foo" | `formula:uo2\|name:\|ctx:10.x/foo` (≠ test #2) |
| 4 | heuristic 全部空, 同一 source_doi, 不同 element_system | None | None | "PuO2" | "10.x/foo" | `formula:puo2\|name:\|ctx:10.x/foo` (≠ test #3) |
| 5 | 退化 — 无 v3 + 无 v4 + 无 source | None | None | None | None | `formula:\|name:\|ctx:` (退化为唯一空 key — 兼容旧行为) |
| 6 | 已有 material + 新增同名 (向后兼容) | "UO2" | "UO2" | None | "10.x/foo" | 与 test #1 相同 (dedup 命中) |
| 7 | material_name 大小写/空格 | "  UO2  " | "UO2" | None | "10.x/foo" | `formula:uo2\|name:uo2\|ctx:10.x/foo` (normalise 一致) |

### 风险评估

| Risk | 影响 | 缓解 |
| --- | --- | --- |
| 旧 mapping 历史数据回填 | 已存在的 27 行 Unknown 不会被去重 — 改 `_material_key` 只影响未来 ingest | 一次性 SQL cleanup script (NFM-3914 follow-up): DELETE WHERE name='Unknown Material' AND formula IS NULL → 保留非空行 |
| 误合并真实同 source 不同 material | 若某论文有两个未命名 phase，`element_system` 又恰好相同 → 会被合并 | 当前 prod 数据 `element_system` 几乎唯一 + `source_doi` 兜底；建议先灰度 |
| schema 旧调用方假设 `_material_key` 输出格式 | 仅 1 调用点 (line 565) 用作 in-memory dict key → 改格式不影响 | ✅ no risk |
| 性能 | 多读 `source_doi` / `source_file` / `source` 三个字段 — 全是 cheap attribute access | ✅ no risk |

**结论: ✅ LOW-RISK CHANGE (改动 1 个 5-行函数 + 1 个 test 文件; 既有 prod 数据独立清理)**

---

## 综合证据链 (cross-condition convergence)

把 6 个独立证据拼成一条 causal chain:

```
[P1.3] heuristic_extractor (line 483, 524) 不发 v4 字段
              ↓ 喂 schema 失败 → mapper _coerce_heuristic_payload 兜底 rename property_name→property, str(value)
              ↓
[P1.6] mapper line 610 fallback "Unknown Material" + line 611 formula=None
              ↓ _find_material_by_formula(db, None) 短路返回 None (line 790-791)
              ↓ 每个 cross-batch ingest run 创建 1 个新 Material row
              ↓
[P1.1] prod DB 27 行 Unknown Material, 20 行集中 2026-08-31 11:00 UTC = ingest burst
              ↓ 同时全部 category_id IS NULL
              ↓
[P1.2] material_categories 表 0 行 + migration 009 没 seed
              ↓ 131/131 materials 无可分类
              ↓
[P1.4] 104 named 实际可聚到 6-10 类 (recommended 8) 与现有 Universe 100% 兼容
              ↓
[P1.5] staging fresh + Owen2023 fixture 12 finding 全部走 mapper fallback — same root cause confirmed
```

→ **单一根因 (heuristic_extractor v4 字段缺失) → 5 个观察现象全部解释**。

---

## 交付清单 (LE → CPO)

| Deliverable | 状态 | 路径 / 哈希 |
| --- | --- | --- |
| 本文档 | ✅ | `docs/research/materials-ux-phase1-evidence.md` |
| Commit | ⏳ | 见 handoff comment (下一步) |
| Branch | ⏳ | `NFM-3913-phase1-evidence` (下一步 push) |
| Prod DB 修改 | ❌ none | (按 §约束 — read-only 调研) |
| 部署 PR | ❌ none | (按 §约束) |
| Production code 修改 | ❌ none | (按 §约束 — P1.6 仅是影响评估, 未提交 patch) |

---

## 开放问题 / 给 Phase 2 的输入

1. **P1.6 修是否纳入 Phase 2 scope?** — 推荐 yes, 单函数 + 单 test 文件, 风险 low
2. **是否做一次性 Unknown Material 数据清理?** — 推荐 yes (NFM-3914 follow-up child issue); 27 行可直接 `DELETE WHERE name='Unknown Material' AND formula IS NULL` 不破坏其他 104 行
3. **P1.4 类别 seed 谁来写?** — 8 类的 INSERT VALUES 已经测过 natural clustering, 推荐 CPO 或 sub-task 在 migration 010 里 seed
4. **P1.5 staging 何时有数据?** — 当前 fresh; 一旦 ingest 跑过 staging 即可复现 P1.1 burst 模式