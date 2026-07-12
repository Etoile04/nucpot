# NucPot MVP 改进迭代 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复测试报告中发现的 14 项改进建议（2×P0 + 7×P1 + 5×P2），按优先级分三个迭代完成。

**Architecture:** 在现有 Next.js 16 + Supabase + Tailwind CSS 项目上迭代，所有前端改进通过新增/修改 React 组件实现，API 层改进通过修改 route handlers 和 Supabase 查询实现。每个 Task 独立可测试。

**Tech Stack:** Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4, Supabase (local), Vitest + Testing Library

---

## File Structure

### 新增文件
- `src/components/Pagination.tsx` — 通用分页组件
- `src/components/ElementFilter.tsx` — 动态元素筛选器组件
- `src/components/SkeletonCard.tsx` — 骨架屏卡片
- `src/components/CompareButton.tsx` — 对比功能触发按钮
- `src/app/compare/page.tsx` — 势函数对比页
- `src/app/not-found.tsx` — 全局 404 页面
- `src/app/error.tsx` — 全局错误页面
- `src/app/loading.tsx` — 全局加载页面
- `src/app/api/auth/my-contributions/route.ts` — 用户贡献查询 API
- `tests/api/pagination.test.ts` — 分页 API 测试
- `tests/api/my-contributions.test.ts` — 贡献查询 API 测试

### 修改文件
- `src/app/browse/page.tsx` — 添加分页 + 动态元素筛选
- `src/app/search/page.tsx` — 添加分页 + 动态元素筛选 + 排序 + 温度过滤
- `src/app/api/potentials/route.ts` — 添加温度范围过滤
- `src/app/potential/[id]/page.tsx` — 验证性质结构化 + LAMMPS 模板
- `src/app/upload/page.tsx` — 补全上传表单字段
- `src/app/profile/page.tsx` — 增加我的贡献列表
- `src/components/Nav.tsx` — 移动端适配
- `src/app/layout.tsx` — 添加 metadata

---

## 迭代一：P0 关键修复（2 Tasks）

### Task 1: 浏览/检索页添加分页 UI

**Files:**
- Create: `src/components/Pagination.tsx`
- Modify: `src/app/browse/page.tsx`
- Modify: `src/app/search/page.tsx`
- Test: `tests/api/pagination.test.ts`

- [ ] **Step 1: 编写分页组件测试**

```typescript
// tests/components/Pagination.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Pagination from '@/components/Pagination'

describe('Pagination', () => {
  it('renders page numbers with current page highlighted', () => {
    render(<Pagination currentPage={2} totalPages={5} onPageChange={() => {}} />)
    expect(screen.getByText('2')).toHaveClass('bg-blue-600')
    expect(screen.getByText('1')).toBeTruthy()
    expect(screen.getByText('5')).toBeTruthy()
  })

  it('disables previous button on first page', () => {
    render(<Pagination currentPage={1} totalPages={5} onPageChange={() => {}} />)
    const prevBtn = screen.getByLabelText('上一页')
    expect(prevBtn).toBeDisabled()
  })

  it('disables next button on last page', () => {
    render(<Pagination currentPage={5} totalPages={5} onPageChange={() => {}} />)
    const nextBtn = screen.getByLabelText('下一页')
    expect(nextBtn).toBeDisabled()
  })

  it('calls onPageChange when clicking a page number', () => {
    const onPageChange = vi.fn()
    render(<Pagination currentPage={1} totalPages={5} onPageChange={onPageChange} />)
    fireEvent.click(screen.getByText('3'))
    expect(onPageChange).toHaveBeenCalledWith(3)
  })

  it('shows ellipsis for large page counts', () => {
    render(<Pagination currentPage={1} totalPages={20} onPageChange={() => {}} />)
    expect(screen.getByText('...')).toBeTruthy()
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx vitest run tests/components/Pagination.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: 实现分页组件**

```typescript
// src/components/Pagination.tsx
'use client'

interface PaginationProps {
  currentPage: number
  totalPages: number
  onPageChange: (page: number) => void
}

export default function Pagination({ currentPage, totalPages, onPageChange }: PaginationProps) {
  if (totalPages <= 1) return null

  const getVisiblePages = (): (number | 'ellipsis')[] => {
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)

    const pages: (number | 'ellipsis')[] = [1]
    if (currentPage > 3) pages.push('ellipsis')

    const start = Math.max(2, currentPage - 1)
    const end = Math.min(totalPages - 1, currentPage + 1)
    for (let i = start; i <= end; i++) pages.push(i)

    if (currentPage < totalPages - 2) pages.push('ellipsis')
    pages.push(totalPages)

    return pages
  }

  const btnBase = 'px-3 py-1.5 rounded-lg text-sm transition'
  const btnActive = `${btnBase} bg-blue-600 text-white`
  const btnInactive = `${btnBase} bg-gray-800 text-gray-300 hover:bg-gray-700`
  const btnDisabled = `${btnBase} bg-gray-800 text-gray-600 cursor-not-allowed`

  return (
    <nav aria-label="分页导航" className="flex items-center justify-center gap-1.5 mt-6">
      <button
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage === 1}
        className={currentPage === 1 ? btnDisabled : btnInactive}
        aria-label="上一页"
      >
        ← 上一页
      </button>

      {getVisiblePages().map((p, i) =>
        p === 'ellipsis' ? (
          <span key={`e${i}`} className="px-2 text-gray-500">...</span>
        ) : (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            className={p === currentPage ? btnActive : btnInactive}
            aria-label={`第 ${p} 页`}
            aria-current={p === currentPage ? 'page' : undefined}
          >
            {p}
          </button>
        )
      )}

      <button
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage === totalPages}
        className={currentPage === totalPages ? btnDisabled : btnInactive}
        aria-label="下一页"
      >
        下一页 →
      </button>
    </nav>
  )
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npx vitest run tests/components/Pagination.test.tsx`
Expected: 5/5 PASS

- [ ] **Step 5: 修改 browse 页面集成分页**

在 `src/app/browse/page.tsx` 中：
1. 添加 `page` state（初始值从 URL searchParams 读取）
2. 将 `page` 参数传入 API 请求
3. 在结果列表下方渲染 `<Pagination>` 组件
4. `onPageChange` 更新 state 并触发 API 重新请求
5. 滚动到页面顶部

- [ ] **Step 6: 修改 search 页面集成分页**

与 browse 页面同样的修改。

- [ ] **Step 7: 运行全量测试确认无回归**

Run: `npx vitest run`
Expected: 所有测试通过（31 existing + 5 new）

- [ ] **Step 8: Commit**

```bash
git add src/components/Pagination.tsx src/app/browse/page.tsx src/app/search/page.tsx tests/components/Pagination.test.tsx
git commit -m "feat: add pagination to browse and search pages (P0)"
```

---

### Task 2: 元素筛选器改为动态列表

**Files:**
- Create: `src/components/ElementFilter.tsx`
- Modify: `src/app/browse/page.tsx`
- Modify: `src/app/search/page.tsx`

- [ ] **Step 1: 编写元素筛选器测试**

```typescript
// tests/components/ElementFilter.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ElementFilter from '@/components/ElementFilter'

describe('ElementFilter', () => {
  const allElements = ['U', 'Zr', 'Mo', 'Nb', 'O', 'Fe', 'He', 'Pu', 'Si', 'C']

  it('renders all elements as buttons', () => {
    render(
      <ElementFilter
        allElements={allElements}
        selected={[]}
        onToggle={() => {}}
      />
    )
    expect(screen.getByText('U')).toBeTruthy()
    expect(screen.getByText('Pu')).toBeTruthy()
    expect(screen.getByText('C')).toBeTruthy()
  })

  it('highlights selected elements', () => {
    render(
      <ElementFilter
        allElements={allElements}
        selected={['U', 'Zr']}
        onToggle={() => {}}
      />
    )
    expect(screen.getByText('U')).toHaveClass('bg-blue-600')
    expect(screen.getByText('Mo')).not.toHaveClass('bg-blue-600')
  })

  it('filters elements by search input', () => {
    render(
      <ElementFilter
        allElements={allElements}
        selected={[]}
        onToggle={() => {}}
      />
    )
    const input = screen.getByPlaceholderText('搜索元素...')
    fireEvent.change(input, { target: { value: 'zr' } })
    expect(screen.getByText('Zr')).toBeTruthy()
    expect(screen.queryByText('U')).toBeNull()
  })

  it('calls onToggle when clicking an element', () => {
    const onToggle = vi.fn()
    render(
      <ElementFilter
        allElements={allElements}
        selected={[]}
        onToggle={onToggle}
      />
    )
    fireEvent.click(screen.getByText('U'))
    expect(onToggle).toHaveBeenCalledWith('U')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx vitest run tests/components/ElementFilter.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现元素筛选器组件**

```typescript
// src/components/ElementFilter.tsx
'use client'

import { useState } from 'react'

interface ElementFilterProps {
  allElements: string[]
  selected: string[]
  onToggle: (element: string) => void
}

export default function ElementFilter({ allElements, selected, onToggle }: ElementFilterProps) {
  const [search, setSearch] = useState('')

  const filtered = search
    ? allElements.filter(e => e.toLowerCase().includes(search.toLowerCase()))
    : allElements

  return (
    <div>
      <input
        type="text"
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="搜索元素..."
        className="w-full px-2 py-1.5 mb-2 rounded bg-gray-700 border border-gray-600 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
        aria-label="搜索元素"
      />
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="元素选择">
        {filtered.map(el => (
          <button
            key={el}
            onClick={() => onToggle(el)}
            className={`px-2.5 py-1 rounded-full text-xs font-medium border transition ${
              selected.includes(el)
                ? 'bg-blue-600 border-blue-500 text-white'
                : 'bg-gray-700 border-gray-600 text-gray-300 hover:border-blue-500/50'
            }`}
            aria-pressed={selected.includes(el)}
          >
            {el}
          </button>
        ))}
        {filtered.length === 0 && (
          <span className="text-xs text-gray-500">无匹配元素</span>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npx vitest run tests/components/ElementFilter.test.tsx`
Expected: 4/4 PASS

- [ ] **Step 5: 修改 browse 和 search 页面使用动态元素列表**

在 `src/app/browse/page.tsx` 中：
1. 用 `useEffect` 从 `/api/stats` 获取 `elements` 数组（已有此 API）
2. 将硬编码的 `ELEMENTS` 常量替换为动态数据
3. 侧边栏用 `<ElementFilter>` 组件替代现有复选框列表

在 `src/app/search/page.tsx` 中做同样的修改。

- [ ] **Step 6: 运行全量测试**

Run: `npx vitest run`
Expected: 所有测试通过

- [ ] **Step 7: Commit**

```bash
git add src/components/ElementFilter.tsx src/app/browse/page.tsx src/app/search/page.tsx tests/components/ElementFilter.test.tsx
git commit -m "feat: dynamic element filter from API instead of hardcoded list (P0)"
```

---

## 迭代二：P1 功能改进（5 Tasks）

### Task 3: 势函数对比功能

**Files:**
- Create: `src/components/CompareButton.tsx`
- Create: `src/app/compare/page.tsx`

- [ ] **Step 1: 在 browse 和 search 页面添加「加入对比」复选框**

每个势函数卡片右侧添加一个 checkbox，最多选 4 个。选中后底部出现浮动对比栏，点击进入对比页。

- [ ] **Step 2: 实现对比页面**

对比页从 URL query 参数读取势函数 ID 列表（`?ids=id1,id2,id3`），并行获取各势函数详情，以表格形式并排对比关键属性：类型、元素、温度范围、适用相态、验证等级、文件可用性、辐照相关、缺陷数据。

- [ ] **Step 3: 运行全量测试**

Run: `npx vitest run`
Expected: 所有测试通过

- [ ] **Step 4: Commit**

```bash
git add src/components/CompareButton.tsx src/app/compare/page.tsx src/app/browse/page.tsx src/app/search/page.tsx
git commit -m "feat: add potential comparison feature (P1)"
```

---

### Task 4: 验证性质结构化展示

**Files:**
- Modify: `src/app/potential/[id]/page.tsx`

- [ ] **Step 1: 重构 verified_props 展示逻辑**

在详情页的「验证性质」Tab 中：
1. 定义中文标签映射：`lattice_constant` → 晶格常数、`elastic_constants` → 弹性常数、`formation_energy` → 形成能、`cohesive_energy` → 结合能、`bulk_modulus` → 体积模量、`melting_point` → 熔点
2. 将 JSON 展示改为结构化表格：性质名称（中文） | 计算值 | 实验参考值（如有） | 偏差（如有）
3. 偏差用颜色标记：<5% 绿色，5-10% 黄色，>10% 红色
4. 未知性质保持原始 key 名展示

- [ ] **Step 2: 运行全量测试**

Run: `npx vitest run`

- [ ] **Step 3: Commit**

```bash
git add src/app/potential/\[id\]/page.tsx
git commit -m "feat: structured display of verified properties (P1)"
```

---

### Task 5: 上传表单补全缺失字段

**Files:**
- Modify: `src/app/upload/page.tsx`

- [ ] **Step 1: 添加「高级选项」折叠区域**

在现有表单下方增加可折叠区域，包含：
1. 开发者信息：可添加多组「姓名 + 单位」
2. 模拟软件：下拉多选（LAMMPS / GULP / VASP / 其他）
3. 子类型：下拉选项（eam/alloy / eam/fs / meam / snap / rann / buckingham / 其他）
4. 核材料特性勾选框：辐照相关 / 缺陷数据 / 液相数据
5. 适用相态：自由文本输入

- [ ] **Step 2: 更新上传 API 调用，传递新字段**

- [ ] **Step 3: 运行全量测试**

Run: `npx vitest run`

- [ ] **Step 4: Commit**

```bash
git add src/app/upload/page.tsx
git commit -m "feat: complete upload form with missing fields (P1)"
```

---

### Task 6: 贡献审核状态可视化

**Files:**
- Create: `src/app/api/auth/my-contributions/route.ts`
- Modify: `src/app/profile/page.tsx`
- Test: `tests/api/my-contributions.test.ts`

- [ ] **Step 1: 编写 API 测试**

```typescript
// tests/api/my-contributions.test.ts
// 测试：返回当前用户的贡献列表，包含 status 字段
// 测试：未登录返回 401
```

- [ ] **Step 2: 实现 API**

在 `src/app/api/auth/my-contributions/route.ts` 中：
1. 验证用户身份
2. 查询 `potentials` 表中 `extra->>'uploaded_by'` 等于当前用户 ID 的记录
3. 按 `created_at` 降序返回

- [ ] **Step 3: 在 Profile 页面添加「我的贡献」列表**

显示每个贡献的名称、状态（pending/published/rejected）、创建时间。

- [ ] **Step 4: 运行全量测试**

Run: `npx vitest run`

- [ ] **Step 5: Commit**

```bash
git add src/app/api/auth/my-contributions/route.ts src/app/profile/page.tsx tests/api/my-contributions.test.ts
git commit -m "feat: contribution review status in profile page (P1)"
```

---

### Task 7: 错误/空状态/加载页面 + 移动端适配

**Files:**
- Create: `src/app/not-found.tsx`
- Create: `src/app/error.tsx`
- Create: `src/app/loading.tsx`
- Create: `src/components/SkeletonCard.tsx`
- Modify: `src/app/browse/page.tsx` — 移动端侧边栏
- Modify: `src/components/Nav.tsx` — 移动端已有但优化

- [ ] **Step 1: 创建 not-found 页面**

```typescript
// src/app/not-found.tsx
import Link from 'next/link'

export default function NotFound() {
  return (
    <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-6xl font-bold text-gray-700 mb-4">404</h1>
        <p className="text-gray-400 mb-6">页面不存在</p>
        <div className="flex gap-3 justify-center">
          <Link href="/" className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg transition">
            返回首页
          </Link>
          <Link href="/browse" className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg transition">
            浏览势函数
          </Link>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 创建 error 页面**

```typescript
// src/app/error.tsx
'use client'

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-2xl font-bold mb-4">出错了</h1>
        <p className="text-gray-400 mb-6">{error.message || '发生了未知错误'}</p>
        <button onClick={reset} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg transition">
          重试
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: 创建 loading 页面 + 骨架屏组件**

- [ ] **Step 4: browse 页面移动端适配**

将固定 `w-64` 侧边栏改为 md 以上显示侧边栏，md 以下改为顶部可折叠筛选面板。

- [ ] **Step 5: 运行全量测试**

Run: `npx vitest run`

- [ ] **Step 6: Commit**

```bash
git add src/app/not-found.tsx src/app/error.tsx src/app/loading.tsx src/components/SkeletonCard.tsx src/app/browse/page.tsx
git commit -m "feat: error/loading states and mobile responsive layout (P1)"
```

---

## 迭代三：P2 增强功能（3 Tasks，合并为 3 个独立提交）

### Task 8: 温度范围过滤 API + 搜索增强

**Files:**
- Modify: `src/app/api/potentials/route.ts`

- [ ] **Step 1: 实现 temperatureRange 过滤**

在 API route 中添加 PostgreSQL JSONB 查询：
```typescript
if (tempMin) {
  dbQuery = dbQuery.gte('applicability->temperatureRange->0', parseInt(tempMin))
}
if (tempMax) {
  dbQuery = dbQuery.lte('applicability->temperatureRange->1', parseInt(tempMax))
}
```

- [ ] **Step 2: 添加结果排序参数**

支持 `sort` query parameter：`updated` (default) / `name` / `type`

- [ ] **Step 3: 运行全量测试 + Commit**

```bash
git add src/app/api/potentials/route.ts
git commit -m "feat: temperature range filter and sorting in API (P2)"
```

---

### Task 9: LAMMPS 完整输入脚本模板

**Files:**
- Modify: `src/app/potential/[id]/page.tsx`

- [ ] **Step 1: 添加「生成完整脚本」功能**

在「使用方法」Tab 中，除了当前的 pair_style/pair_coeff，增加「生成完整 LAMMPS 输入脚本」按钮，生成包含 `units metal`、`dimension 3`、`boundary p p p`、`atom_style atomic`、`read_data`、`run` 等必要设置的完整模板脚本。

- [ ] **Step 2: 运行测试 + Commit**

```bash
git add src/app/potential/\[id\]/page.tsx
git commit -m "feat: generate complete LAMMPS input script template (P2)"
```

---

### Task 10: SEO + a11y 基础

**Files:**
- Create: `src/app/robots.ts`
- Create: `src/app/sitemap.ts`
- Modify: `src/app/layout.tsx`
- Modify: `src/app/potential/[id]/page.tsx` — dynamic metadata
- Modify: `src/components/Nav.tsx` — aria labels
- Modify: `src/app/browse/page.tsx` — aria groups

- [ ] **Step 1: 添加 robots.ts**

```typescript
// src/app/robots.ts
import { MetadataRoute } from 'next'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: '*', allow: '/', disallow: '/api/' },
    sitemap: 'https://nucpot.org/sitemap.xml',
  }
}
```

- [ ] **Step 2: 添加 sitemap.ts**

静态页面 + 动态势函数详情页 URL 列表。

- [ ] **Step 3: 详情页动态 metadata**

```typescript
// src/app/potential/[id]/page.tsx
export async function generateMetadata({ params }: { params: { id: string } }) {
  const res = await fetch(`${process.env.NEXT_PUBLIC_BASE_URL || ''}/api/potentials/${params.id}`)
  const data = await res.json()
  return {
    title: `${data.display_name || data.name} — NucPot`,
    description: data.description || `${data.elements.join('-')} ${data.type} 势函数`,
  }
}
```

- [ ] **Step 4: a11y 基础 — aria labels**

在 Nav、browse 筛选器、表单等处添加 `aria-label`、`role="group"`、`aria-pressed` 等属性。

- [ ] **Step 5: 运行全量测试 + Commit**

```bash
git add src/app/robots.ts src/app/sitemap.ts src/app/layout.tsx src/app/potential/\[id\]/page.tsx src/components/Nav.tsx src/app/browse/page.tsx
git commit -m "feat: SEO basics and a11y improvements (P2)"
```

---

## 合并流程（所有 Task 完成后）

使用 `superpowers:finishing-a-development-branch` 技能：
1. 运行全量测试确认通过
2. 检查 lint
3. 提供选项：merge / PR / keep / discard
