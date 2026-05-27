# NucPot Phase 2: 验证管线 MVP + 云部署

> **目标**: 在 NucPot 平台上实现势函数自动验证功能，并部署到云端对外提供服务
> **技术栈**: FastAPI + kimpy/ASE + Supabase + Vercel + 云服务器
> **预计工期**: 2 周

---

## 架构概览

```
┌─────────────────────────────────────┐
│     NucPot 前端 (Next.js)            │
│     部署: Vercel                      │
│     - 验证结果展示页                   │
│     - 触发验证按钮                     │
└──────────────┬──────────────────────┘
               │ API 调用
               ▼
┌─────────────────────────────────────┐
│     验证服务 (FastAPI)                │
│     部署: 云服务器 (Docker)            │
│     - 接收验证请求                     │
│     - 调度计算任务                     │
│     - 结果回写 Supabase               │
└──────────────┬──────────────────────┘
               │ Python Worker
               ▼
┌─────────────────────────────────────┐
│     计算引擎                          │
│     - ASE + LAMMPS (备选: kimpy)     │
│     - 晶格常数、弹性常数、空位形成能    │
│     - 生成结构化验证结果               │
└─────────────────────────────────────┘
```

---

## File Structure

```
nucpot/
├── src/                          # 现有 Next.js 前端
│   ├── app/
│   │   ├── verify/               # 新增：验证结果展示页
│   │   │   └── page.tsx
│   │   └── api/
│   │       └── verify/           # 新增：验证 API 代理
│   │           └── route.ts
│   ├── components/
│   │   ├── VerificationBadge.tsx  # 新增：验证等级徽章
│   │   └── VerificationPanel.tsx  # 新增：详情页验证面板
│   └── lib/
│       └── types.ts              # 更新：验证相关类型
├── verify-service/               # 新增：Python 验证服务
│   ├── main.py                   # FastAPI 入口
│   ├── config.py                 # 配置管理
│   ├── models.py                 # Pydantic 数据模型
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── base.py               # Worker 基类
│   │   ├── lattice.py            # 晶格常数计算
│   │   ├── elastic.py            # 弹性常数计算
│   │   └── vacancy.py            # 空位形成能计算
│   ├── runners/
│   │   ├── __init__.py
│   │   ├── ase_runner.py         # ASE 计算后端
│   │   └── lammps_runner.py      # LAMMPS 计算后端 (future)
│   ├── db.py                     # Supabase 客户端
│   ├── requirements.txt
│   ├── Dockerfile
│   └── tests/
│       ├── test_api.py
│       ├── test_lattice.py
│       └── test_elastic.py
├── supabase/
│   └── migrations/
│       └── 002_verifications.sql  # 新增：验证相关表
└── docker-compose.yml            # 新增：本地开发编排
```

---

## Task 1: 技术可行性验证 ✅ (进行中)
- kimpy/kimvv vs ASE 备选方案评估
- 由子智能体执行，输出到 `docs/research/kimpy-feasibility-report.md`

---

## Task 2: 数据库迁移 — verifications 表

**Files:**
- Create: `supabase/migrations/002_verifications.sql`
- Modify: `src/lib/types.ts` — 添加 Verification 类型

**Schema 设计:**

```sql
-- 验证任务表
CREATE TABLE verifications (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  potential_id  UUID NOT NULL REFERENCES potentials(id) ON DELETE CASCADE,
  status        VARCHAR(16) DEFAULT 'pending',  -- pending/running/completed/failed
  requested_by  VARCHAR(64),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  completed_at  TIMESTAMPTZ,
  
  -- 计算结果
  results       JSONB DEFAULT '{}',
  -- { lattice_constant: {value: 3.42, unit: "Å", reference: 3.38, error_pct: 1.18, grade: "A"}, ... }
  
  overall_grade VARCHAR(2),      -- A/B/C/D/F
  summary       TEXT,            -- 人类可读摘要
  error_log     TEXT,            -- 错误日志（失败时）
  compute_time  INTEGER          -- 计算耗时（秒）
);

-- 参考值表（DFT/实验值）
CREATE TABLE reference_values (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  element_system VARCHAR(64) NOT NULL,  -- "U", "U-Mo", "U-Zr", ...
  phase         VARCHAR(32),            -- "BCC", "FCC", "gamma", ...
  property      VARCHAR(64) NOT NULL,   -- "lattice_constant", "C11", "vacancy_formation_energy", ...
  value         DOUBLE PRECISION NOT NULL,
  unit          VARCHAR(16),
  uncertainty   DOUBLE PRECISION,
  temperature   DOUBLE PRECISION,       -- K
  pressure      DOUBLE PRECISION DEFAULT 0,
  source        TEXT,                    -- 论文引用
  source_doi    VARCHAR(128),
  method        VARCHAR(32),            -- "experiment", "DFT", "calculated"
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_verifications_potential ON verifications(potential_id);
CREATE INDEX idx_verifications_status ON verifications(status);
CREATE INDEX idx_ref_element ON reference_values(element_system);
CREATE INDEX idx_ref_property ON reference_values(property);
```

**Acceptance:**
- [ ] 迁移脚本可执行
- [ ] verifications 和 reference_values 表创建成功
- [ ] 种子数据：插入 10+ 条核材料参考值（U, U-Mo, U-Zr, Zr 的晶格常数、弹性常数、空位形成能）
- [ ] TypeScript 类型定义更新

---

## Task 3: Python 验证服务 — FastAPI 骨架 + ASE 计算引擎

**Files:**
- Create: `verify-service/main.py`
- Create: `verify-service/config.py`
- Create: `verify-service/models.py`
- Create: `verify-service/db.py`
- Create: `verify-service/workers/base.py`
- Create: `verify-service/workers/lattice.py`
- Create: `verify-service/workers/elastic.py`
- Create: `verify-service/runners/ase_runner.py`
- Create: `verify-service/requirements.txt`
- Create: `verify-service/tests/test_api.py`
- Create: `verify-service/tests/test_lattice.py`

**API Endpoints:**

```python
POST /api/verify/{potential_id}        # 触发验证（从 Supabase 获取势函数信息）
GET  /api/verify/{id}/status           # 查询验证状态
GET  /api/verify/{id}/results          # 获取验证结果
GET  /api/verify/potential/{pid}/latest  # 获取某势函数最新验证结果
GET  /api/reference/{element_system}    # 查询参考值
GET  /api/health                        # 健康检查
```

**Worker 逻辑:**

1. 从 Supabase 获取势函数元数据（type, elements, file_url, lammps_config）
2. 下载势函数文件（如果是远程 URL）
3. 使用 ASE 创建对应晶体结构
4. 通过 ASE 的 LAMMPS calculator 或内置 calculator 计算属性：
   - **晶格常数**: Birch-Murnaghan 拟合（能量-体积曲线）
   - **弹性常数**: 应变-能量法（6 个独立应变 × 多个应变幅度）
   - **空位形成能**: 移除一个原子后弛豫，计算能量差
5. 从 Supabase reference_values 获取参考值
6. 计算相对误差，评定 A-F 等级
7. 结果回写 verifications 表

**等级评定标准:**
```
A: |error| < 2%
B: |error| < 5%
C: |error| < 10%
D: |error| < 20%
F: |error| >= 20%
```

**Acceptance:**
- [ ] FastAPI 服务可启动
- [ ] /api/health 返回 200
- [ ] 能对已有 EAM 势函数计算晶格常数（纯金属：U, Mo, Zr）
- [ ] 计算结果写入 Supabase verifications 表
- [ ] 3+ 个单元测试通过

---

## Task 4: NucPot 前端 — 验证结果展示

**Files:**
- Create: `src/app/verify/page.tsx` — 验证结果列表页
- Create: `src/components/VerificationBadge.tsx` — 等级徽章组件
- Create: `src/components/VerificationPanel.tsx` — 详情页验证面板
- Modify: `src/app/potential/[id]/page.tsx` — 集成验证面板
- Modify: `src/app/api/verify/route.ts` — API 代理到验证服务
- Create: `src/app/api/verify/[id]/route.ts`

**UI 设计:**

1. **验证徽章**: 在势函数卡片和列表中显示验证等级（A/B/C/D/F + 未验证）
2. **验证面板** (详情页下方):
   - "开始验证" 按钮（登录用户可用）
   - 验证进度条（pending → running → completed/failed）
   - 结果表格：属性名 | 计算值 | 参考值 | 误差 | 等级
   - 综合评级 + 摘要文字
3. **验证历史页** (`/verify`):
   - 最近验证结果列表
   - 按势函数/等级/日期筛选

**Acceptance:**
- [ ] 详情页显示验证面板
- [ ] 可以触发验证（调用 FastAPI 后端）
- [ ] 验证结果以表格形式展示
- [ ] 等级徽章在各处正确显示

---

## Task 5: 参考值种子数据 + 测试数据

**Files:**
- Create: `scripts/seed-reference-values.mjs` — 参考值种子数据脚本
- Create: `verify-service/tests/test_elastic.py` — 弹性常数测试
- Create: `verify-service/tests/fixtures/` — 测试用势函数文件

**参考值数据来源:**
- U (gamma, BCC): a=3.47 Å, C11=119 GPa, C12=103 GPa, C44=76 GPa
- Mo (BCC): a=3.15 Å, C11=450 GPa, C12=173 GPa, C44=126 GPa
- Zr (BCC, high-T): a=3.61 Å, C11=89 GPa
- U-Mo (gamma): a=3.40 Å (approx, depends on Mo%)
- Nb (BCC): a=3.30 Å

**Acceptance:**
- [ ] 15+ 条参考值插入成功
- [ ] 覆盖 5+ 种材料体系
- [ ] 覆盖 3+ 种属性（晶格常数、弹性常数、空位形成能）

---

## Task 6: 集成测试 + 端到端验证

**Files:**
- Create: `verify-service/tests/test_e2e.py`
- Update: `__tests__/e2e/` — 前端 E2E 测试

**测试场景:**
1. 上传势函数 → 触发验证 → 查看结果（完整流程）
2. 已有势函数 → 重新验证 → 结果更新
3. 验证失败（无效势函数文件）→ 错误信息展示
4. 验证进行中 → 刷新页面 → 状态保持

**Acceptance:**
- [ ] 端到端验证流程可完成
- [ ] 4 个测试场景全部通过

---

## Task 7: Docker 化 + 部署配置

**Files:**
- Create: `verify-service/Dockerfile`
- Create: `docker-compose.yml` — 本地开发编排
- Create: `verify-service/.env.example`
- Update: `docs/DEPLOY.md` — 更新部署文档

**Docker 架构:**
```yaml
services:
  nucpot-web:       # Next.js (生产模式)
  verify-service:   # FastAPI 验证服务
  supabase-db:      # PostgreSQL (或用 Supabase Cloud)
```

**部署方案:**

| 组件 | 方案 | 理由 |
|------|------|------|
| Next.js 前端 | Vercel | 免费、CDN、自动部署 |
| FastAPI 验证服务 | 云服务器 Docker | 需要计算资源、长任务 |
| 数据库 | Supabase Cloud | 免费 tier 够用 |
| 势函数文件 | Supabase Storage | 已集成 |

**Acceptance:**
- [ ] docker-compose up 可一键启动全部服务
- [ ] FastAPI Dockerfile 构建成功
- [ ] Vercel 部署配置就绪
- [ ] 部署文档更新

---

## Task 8: 最终验证 + 分支合并 + 上线

- [ ] 所有测试通过（前端 + 后端）
- [ ] 生产环境部署成功
- [ ] 可以从外网访问并触发验证
- [ ] 使用 finishing-a-development-branch 合并
- [ ] 更新 README 路线图

---

## 技术决策备忘

| 决策 | 选择 | 理由 |
|------|------|------|
| 计算引擎 | ASE + LAMMPS calculator | 跨平台、Python 原生、活跃社区 |
| 任务调度 | 同步（MVP）→ Celery（后续） | MVP 先简单做，验证需求后再加队列 |
| 验证等级 | A-F 五级制 | 参考 OpenKIM + 直观 |
| 参考值存储 | Supabase JSONB | 灵活、易扩展 |
| 部署分离 | Vercel(前端) + 云服务器(验证) | 计算任务不适合 Serverless |
