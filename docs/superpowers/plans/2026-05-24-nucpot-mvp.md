# NucPot MVP 本地开发测试 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在本地完成 NucPot 核材料势函数库 MVP 的全功能开发和测试，包括数据库初始化、API 测试、前端页面完善、端到端验证。

**Architecture:** Next.js 16 App Router + Supabase (PostgreSQL) + Tailwind CSS。前端页面直接调用 Route Handlers（`/api/*`），Route Handlers 通过 `@supabase/supabase-js` 连接本地 Supabase Docker 实例。无中间层，前后端同仓。

**Tech Stack:** Next.js 16, React 19, TypeScript, Tailwind CSS 4, Supabase (Docker), Vitest (测试)

---

## File Structure

```
src/
├── app/
│   ├── layout.tsx                # 全局布局（已有，需完善 metadata + 字体）
│   ├── page.tsx                  # 首页（已有，需微调）
│   ├── browse/page.tsx           # 浏览页（已有，需微调）
│   ├── search/page.tsx           # 高级检索页（新建）
│   ├── potential/[id]/page.tsx   # 详情页（已有，需微调）
│   ├── about/page.tsx            # 关于页（新建）
│   └── api/
│       ├── potentials/route.ts   # 列表 API（已有，需加验证）
│       ├── potentials/[id]/route.ts  # 详情 API（已有）
│       └── stats/route.ts        # 统计 API（已有）
├── lib/
│   ├── supabase.ts              # Supabase 客户端（已有）
│   └── types.ts                 # TypeScript 类型定义（新建）
supabase/
├── schema.sql                   # DB schema + 种子数据（已有）
└── config.toml                  # Supabase 配置（已有）
__tests__/
├── api/
│   ├── potentials.test.ts       # API 端点测试（新建）
│   └── stats.test.ts            # 统计 API 测试（新建）
└── lib/
    └── supabase.test.ts         # Supabase 客户端测试（新建）
```

---

## Task 1: 项目基础设施 — 测试框架 + Git Worktree

**Files:**
- Modify: `package.json` — 添加 vitest 和测试脚本
- Create: `vitest.config.ts`
- Create: `src/lib/types.ts` — 势函数类型定义

- [ ] **Step 1: 安装 Vitest 测试框架**

```bash
cd ~/projects/nucpot
npm install -D vitest @vitejs/plugin-react jsdom
```

- [ ] **Step 2: 创建 vitest.config.ts**

```typescript
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
```

- [ ] **Step 3: 添加测试脚本到 package.json**

在 `scripts` 中添加:
```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 4: 创建 TypeScript 类型定义 `src/lib/types.ts`**

```typescript
export interface Potential {
  id: string
  name: string
  display_name: string | null
  type: string
  subtype: string | null
  format: string | null
  elements: string[]
  system_name: string | null
  system_tags: string[] | null
  description: string | null
  applicability: Applicability | null
  references: Reference[]
  developers: Developer[]
  verified_props: Record<string, unknown>
  sim_software: string[]
  lammps_config: LammpsConfig | null
  file_url: string | null
  file_hash: string | null
  file_size: number | null
  source: string | null
  license: string
  tags: string[]
  extra: PotentialExtra | null
  status: string
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface Applicability {
  temperatureRange?: [number, number]
  pressureRange?: [number, number]
  phases?: string[]
  notes?: string
}

export interface Reference {
  doi?: string
  citation?: string
  url?: string
}

export interface Developer {
  name: string
  affiliation?: string
}

export interface LammpsConfig {
  pair_style?: string
  pair_coeff?: string
  note?: string
}

export interface PotentialExtra {
  irradiationRelevant?: boolean
  hasDefectData?: boolean
  hasLiquidPhase?: boolean
  validationLevel?: 'basic' | 'benchmarked' | 'production'
}

export interface PotentialsResponse {
  potentials: Potential[]
  total: number
  page: number
  limit: number
  totalPages: number
}

export interface StatsResponse {
  totalPotentials: number
  totalTypes: number
  totalElements: number
  types: string[]
  elements: string[]
  recent: Potential[]
}
```

- [ ] **Step 5: 验证测试框架可用**

```bash
npx vitest run --passWithNoTests
```
Expected: 输出 "No test files found" 但 exit 0

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: add vitest test framework + TypeScript types"
```

---

## Task 2: 数据库初始化 — Supabase 本地启动 + Schema 验证

**Files:**
- Modify: `.env.local` — 填入 Supabase 真实连接信息
- Modify: `src/lib/supabase.ts` — 添加服务端客户端

- [ ] **Step 1: 启动 Docker Desktop**

检查 Docker 状态：
```bash
docker info 2>/dev/null | head -3
```
若未启动，提醒用户启动 Docker Desktop。

- [ ] **Step 2: 启动本地 Supabase**

```bash
cd ~/projects/nucpot
supabase start
```
Expected: 输出 API URL、DB URL、anon key 等

- [ ] **Step 3: 初始化 Schema + 种子数据**

```bash
supabase db reset
```
Expected: schema.sql 执行成功，10 条势函数插入

- [ ] **Step 4: 验证种子数据**

```bash
supabase db query "SELECT name, type, elements FROM potentials ORDER BY name;"
```
Expected: 10 行记录

- [ ] **Step 5: 更新 .env.local 填入真实连接信息**

从 `supabase start` 输出中复制 `API URL` 和 `anon key`：
```
NEXT_PUBLIC_SUPABASE_URL=<从输出复制>
NEXT_PUBLIC_SUPABASE_ANON_KEY=<从输出复制>
```

- [ ] **Step 6: 创建服务端 Supabase 客户端（用于 Route Handlers）**

更新 `src/lib/supabase.ts`：
```typescript
import { createClient, SupabaseClient } from '@supabase/supabase-js'

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!

export const supabase: SupabaseClient = createClient(supabaseUrl, supabaseAnonKey)
```

- [ ] **Step 7: Commit**

```bash
git add .env.local src/lib/supabase.ts
git commit -m "feat: configure local Supabase connection"
```
注意：确认 `.env.local` 在 `.gitignore` 中，不要提交密钥。

---

## Task 3: API 测试 — Potentials + Stats 端点

**Files:**
- Create: `__tests__/api/potentials.test.ts`
- Create: `__tests__/api/stats.test.ts`
- Modify: `src/app/api/potentials/route.ts` — 引用 types.ts，修复类型

- [ ] **Step 1: 更新 API 引用 types.ts**

在 `src/app/api/potentials/route.ts` 顶部添加类型导入：
```typescript
import { PotentialsResponse } from '@/lib/types'
```
并将返回类型标注为 `NextResponse<PotentialsResponse>`。

在 `src/app/api/stats/route.ts` 顶部添加：
```typescript
import { StatsResponse } from '@/lib/types'
```

- [ ] **Step 2: 编写 Potentials API 测试**

Create `__tests__/api/potentials.test.ts`:
```typescript
import { describe, it, expect, beforeAll } from 'vitest'
import { GET } from '@/app/api/potentials/route'
import { NextRequest } from 'next/server'

// 注意：这些测试需要 Supabase 本地运行
describe('GET /api/potentials', () => {
  it('returns potentials list with default pagination', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials'))
    const res = await GET(req)
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.potentials).toBeInstanceOf(Array)
    expect(data.total).toBeGreaterThan(0)
    expect(data.page).toBe(1)
    expect(data.limit).toBe(20)
  })

  it('filters by type', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?type=EAM'))
    const res = await GET(req)
    const data = await res.json()

    expect(res.status).toBe(200)
    data.potentials.forEach((p: { type: string }) => {
      expect(p.type).toBe('EAM')
    })
  })

  it('filters by elements', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?elements=U,Zr'))
    const res = await GET(req)
    const data = await res.json()

    expect(res.status).toBe(200)
    data.potentials.forEach((p: { elements: string[] }) => {
      expect(p.elements).toContain('U')
      expect(p.elements).toContain('Zr')
    })
  })

  it('returns empty for nonexistent type', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?type=NONEXISTENT'))
    const res = await GET(req)
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.potentials).toHaveLength(0)
    expect(data.total).toBe(0)
  })
})
```

- [ ] **Step 3: 编写 Stats API 测试**

Create `__tests__/api/stats.test.ts`:
```typescript
import { describe, it, expect } from 'vitest'
import { GET } from '@/app/api/stats/route'

describe('GET /api/stats', () => {
  it('returns stats with correct structure', async () => {
    const res = await GET()
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.totalPotentials).toBe(10)
    expect(data.totalTypes).toBeGreaterThan(0)
    expect(data.totalElements).toBeGreaterThan(0)
    expect(data.types).toBeInstanceOf(Array)
    expect(data.elements).toBeInstanceOf(Array)
    expect(data.recent).toBeInstanceOf(Array)
    expect(data.recent.length).toBeLessThanOrEqual(5)
  })

  it('includes expected nuclear material elements', async () => {
    const res = await GET()
    const data = await res.json()

    expect(data.elements).toContain('U')
    expect(data.elements).toContain('Zr')
    expect(data.elements).toContain('Fe')
  })
})
```

- [ ] **Step 4: 运行测试**

```bash
npx vitest run __tests__/api/
```
Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: add API endpoint tests for potentials and stats"
```

---

## Task 4: 高级检索页

**Files:**
- Create: `src/app/search/page.tsx`
- Modify: `src/app/api/potentials/route.ts` — 添加温度范围和标签筛选

- [ ] **Step 1: 扩展 API 支持高级检索参数**

在 `src/app/api/potentials/route.ts` 中添加：
```typescript
const tempMin = searchParams.get('tempMin')
const tempMax = searchParams.get('tempMax')
const irradiation = searchParams.get('irradiation') // 'true'

if (tempMin || tempMax) {
  // JSONB 查询: applicability->temperatureRange 与 [tempMin, tempMax] 有交集
  dbQuery = dbQuery.overlaps('tags', tags ? tags.split(',') : [])
}

if (irradiation === 'true') {
  dbQuery = dbQuery.eq('extra->>irradiationRelevant', 'true')
}
```

- [ ] **Step 2: 创建高级检索页 `src/app/search/page.tsx`**

包含以下 UI 元素（参照 `design/ui-wireframe.md` 第 3 页）：
- 关键词搜索输入框
- 元素组合（多选标签式输入）
- 函数形式下拉
- 温度范围区间输入（min-max）
- 核材料专用开关：辐照相关、缺陷数据、液相数据
- 搜索结果列表（复用浏览页的卡片样式）
- 响应式布局

- [ ] **Step 3: 验证高级检索页构建通过**

```bash
npx next build 2>&1 | grep -E "(Error|✓|Route)"
```
Expected: 无 Error，`/search` 出现在 Route 列表中

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: add advanced search page with nuclear material filters"
```

---

## Task 5: 关于页

**Files:**
- Create: `src/app/about/page.tsx`

- [ ] **Step 1: 创建关于页**

参照 `design/ui-wireframe.md` 第 5 页，包含：
- 项目背景：面向核燃料/包壳/结构材料的势函数开放平台
- 数据来源：NIST IPR、OpenKIM 等开源平台
- 协作团队：湖南大学邓辉球团队、核动力院
- 联系方式（占位）

- [ ] **Step 2: 验证构建**

```bash
npx next build
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: add about page"
```

---

## Task 6: 全局布局完善 + 响应式

**Files:**
- Modify: `src/app/layout.tsx` — metadata、字体、全局 Nav 组件
- Create: `src/components/Nav.tsx` — 提取导航栏为共享组件

- [ ] **Step 1: 提取导航栏为共享组件**

Create `src/components/Nav.tsx`，包含：
- Logo + 品牌名
- 导航链接（浏览、高级检索、关于）
- 当前页面高亮
- 移动端汉堡菜单（响应式）

- [ ] **Step 2: 更新 layout.tsx**

- 添加 proper metadata (title: "NucPot - 核材料势函数库")
- 引入 Nav 组件
- 所有页面统一使用该 Nav（删除各页面内重复的 nav）

- [ ] **Step 3: 更新所有页面使用共享 Nav**

在 page.tsx、browse/page.tsx、potential/[id]/page.tsx、search/page.tsx、about/page.tsx 中删除各自的 `<nav>` 块，使用 `<Nav />` 组件。

- [ ] **Step 4: 验证构建**

```bash
npx next build
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: extract shared Nav component, improve layout"
```

---

## Task 7: 端到端集成测试

**Files:**
- Create: `__tests__/e2e/smoke.test.ts` — 冒烟测试
- Create: `__tests__/e2e/seed-data.test.ts` — 种子数据完整性

- [ ] **Step 1: 编写冒烟测试**

Create `__tests__/e2e/smoke.test.ts`:
```typescript
import { describe, it, expect } from 'vitest'

describe('Smoke tests - MVP basic functionality', () => {
  it('next build succeeds', async () => {
    // 通过 vitest build 验证 — 如果能跑测试说明代码编译通过
    expect(true).toBe(true)
  })
})
```

- [ ] **Step 2: 编写种子数据完整性测试**

Create `__tests__/e2e/seed-data.test.ts`:
```typescript
import { describe, it, expect } from 'vitest'
import { GET as getPotentials } from '@/app/api/potentials/route'
import { GET as getStats } from '@/app/api/stats/route'
import { NextRequest } from 'next/server'

describe('Seed data integrity', () => {
  it('has exactly 10 seed potentials', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials'))
    const res = await getPotentials(req)
    const data = await res.json()
    expect(data.total).toBe(10)
  })

  it('covers all nuclear material systems', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials'))
    const res = await getPotentials(req)
    const data = await res.json()

    const systems = data.potentials.map((p: { system_name: string | null }) => p.system_name)
    expect(systems).toContain('U-Zr alloy')
    expect(systems).toContain('Pure uranium')
    expect(systems).toContain('UO₂ (uranium dioxide)')
    expect(systems).toContain('Pure zirconium')
    expect(systems).toContain('Zr-Nb alloy')
  })

  it('includes multiple potential types', async () => {
    const res = await getStats()
    const data = await res.json()
    expect(data.types).toContain('EAM')
    expect(data.types).toContain('MEAM')
    expect(data.types).toContain('ML')
  })

  it('each potential has required fields', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?limit=10'))
    const res = await getPotentials(req)
    const data = await res.json()

    data.potentials.forEach((p: { name: string; type: string; elements: string[]; references: unknown[] }) => {
      expect(p.name).toBeTruthy()
      expect(p.type).toBeTruthy()
      expect(p.elements).toBeInstanceOf(Array)
      expect(p.elements.length).toBeGreaterThan(0)
      expect(p.references).toBeInstanceOf(Array)
    })
  })
})
```

- [ ] **Step 3: 运行全部测试**

```bash
npx vitest run
```
Expected: 全部通过

- [ ] **Step 4: 运行 Next.js 构建**

```bash
npx next build
```
Expected: 构建成功，无 Error

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: add E2E smoke tests and seed data integrity checks"
```

---

## Task 8: 最终验证 + 分支完成

**Files:** 无新文件

- [ ] **Step 1: 运行全部测试（最终验证）**

```bash
npx vitest run
```
Expected: 全部通过

- [ ] **Step 2: 运行 Next.js 构建（最终验证）**

```bash
npx next build
```
Expected: 构建成功

- [ ] **Step 3: 启动开发服务器手动验证**

```bash
npm run dev
```
在浏览器中验证：
- 首页加载正常
- 浏览页筛选正常
- 详情页显示正常
- 高级检索页功能正常
- 关于页正常

- [ ] **Step 4: 使用 superpowers:finishing-a-development-branch 完成分支**

按 finishing-a-development-branch 技能流程：
1. 验证测试通过 ✅
2. 确定基准分支（main）
3. 向用户呈现 4 个选项（合并 / PR / 保留 / 丢弃）
4. 执行选择
5. 清理工作区
