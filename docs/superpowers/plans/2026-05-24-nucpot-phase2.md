# NucPot Phase 2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 MVP 基础上增加用户认证、势函数批量导入和上传贡献功能，让平台从静态展示升级为可交互的社区平台。

**Architecture:** 在 MVP 的 Supabase 基础上启用 Auth 模块（email/password），增加 `profiles` 和 `contributions` 表。前端增加登录/注册页、管理后台、上传表单。API 增加写入端点（需认证）。势函数批量扩展通过脚本从 NIST IPR 系统性抓取。

**Tech Stack:** Next.js 16 + Supabase Auth + Supabase Storage + Vitest

---

## File Structure

```
src/
├── app/
│   ├── layout.tsx                # 更新：Auth provider
│   ├── page.tsx                  # 更新：显示登录状态
│   ├── login/page.tsx            # 新建：登录/注册页
│   ├── admin/page.tsx            # 新建：管理后台
│   ├── upload/page.tsx           # 新建：势函数上传页
│   ├── browse/page.tsx           # 更新：显示贡献者
│   ├── potential/[id]/page.tsx   # 更新：编辑/删除按钮
│   └── api/
│       ├── auth/                 # 新建
│       │   ├── login/route.ts
│       │   ├── register/route.ts
│       │   └── profile/route.ts
│       ├── potentials/
│       │   ├── route.ts          # 更新：POST 需认证
│       │   ├── [id]/route.ts     # 更新：PUT/DELETE 需认证
│       │   └── import/route.ts   # 新建：批量导入
│       └── stats/route.ts
├── components/
│   ├── Nav.tsx                   # 更新：显示用户名/登录按钮
│   ├── AuthProvider.tsx          # 新建：Supabase auth context
│   ├── PotentialForm.tsx         # 新建：势函数上传表单
│   └── ProtectedRoute.tsx       # 新建：路由守卫
├── lib/
│   ├── supabase.ts              # 更新：server client
│   ├── types.ts                 # 更新：新增 User/Contribution 类型
│   └── auth.ts                  # 新建：auth helpers
supabase/
├── migrations/                  # 新建
│   └── 001_auth_and_profiles.sql
└── schema.sql                   # 保持
scripts/
├── seed-db.mjs                  # 已有
└── expand-potentials.mjs        # 新建：从 NIST IPR 批量导入
__tests__/
├── api/
│   ├── auth.test.ts             # 新建
│   └── upload.test.ts           # 新建
└── e2e/
    └── phase2-smoke.test.ts     # 新建
```

---

## Task 1: Supabase Auth 配置 + 数据库迁移

**Files:**
- Create: `supabase/migrations/001_auth_and_profiles.sql`
- Modify: `src/lib/types.ts` — 添加 User/Profile 类型
- Modify: `src/lib/supabase.ts` — 添加 server client + admin client

- [ ] **Step 1: 创建数据库迁移脚本**

Create `supabase/migrations/001_auth_and_profiles.sql`:

```sql
-- 用户 profile 表（关联 Supabase Auth）
CREATE TABLE profiles (
  id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  username    VARCHAR(64) UNIQUE NOT NULL,
  full_name   VARCHAR(128),
  email       TEXT,
  role        VARCHAR(16) DEFAULT 'contributor',  -- admin | contributor | viewer
  avatar_url  TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 贡献记录表
CREATE TABLE contributions (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  potential_id  UUID REFERENCES potentials(id) ON DELETE SET NULL,
  user_id       UUID REFERENCES profiles(id) ON DELETE SET NULL,
  action        VARCHAR(32) NOT NULL,  -- create | update | review
  status        VARCHAR(16) DEFAULT 'pending',  -- pending | approved | rejected
  notes         TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- RLS: profiles
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Profiles are viewable by everyone" ON profiles FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Users can update own profile" ON profiles FOR UPDATE TO authenticated USING (auth.uid() = id);
CREATE POLICY "Users can insert own profile" ON profiles FOR INSERT TO authenticated WITH CHECK (auth.uid() = id);

-- RLS: contributions
ALTER TABLE contributions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Contributions viewable by everyone" ON contributions FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Authenticated users can create contributions" ON contributions FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "Admin can update contributions" ON contributions FOR UPDATE TO authenticated USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = 'admin')
);

-- RLS: potentials — 允许认证用户创建
CREATE POLICY "Authenticated users can create potentials" ON potentials FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "Admin can update/delete potentials" ON potentials FOR UPDATE TO authenticated USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = 'admin')
);
CREATE POLICY "Admin can delete potentials" ON potentials FOR DELETE TO authenticated USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = 'admin')
);

-- Indexes
CREATE INDEX idx_contributions_user ON contributions(user_id);
CREATE INDEX idx_contributions_potential ON contributions(potential_id);
CREATE INDEX idx_profiles_role ON profiles(role);
```

- [ ] **Step 2: 添加类型定义**

在 `src/lib/types.ts` 中追加:

```typescript
export interface Profile {
  id: string
  username: string
  full_name: string | null
  email: string | null
  role: 'admin' | 'contributor' | 'viewer'
  avatar_url: string | null
  created_at: string
  updated_at: string
}

export interface Contribution {
  id: string
  potential_id: string | null
  user_id: string | null
  action: 'create' | 'update' | 'review'
  status: 'pending' | 'approved' | 'rejected'
  notes: string | null
  created_at: string
}
```

- [ ] **Step 3: 运行迁移**

```bash
cd /Users/lwj04/Projects/nucpot
node -e "
const pg = require('pg');
const fs = require('fs');
const client = new pg.Client({ connectionString: process.env.DATABASE_URL || 'postgresql://postgres:postgres@127.0.0.1:54322/postgres' });
client.connect().then(() => {
  const sql = fs.readFileSync('supabase/migrations/001_auth_and_profiles.sql', 'utf8');
  return client.query(sql);
}).then(() => { console.log('✅ Migration applied'); return client.end(); })
  .catch(e => { console.error('❌', e.message); process.exit(1); });
"
```

- [ ] **Step 4: 验证表创建**

```bash
node -e "
const pg = require('pg');
const client = new pg.Client({ connectionString: process.env.DATABASE_URL || 'postgresql://postgres:postgres@127.0.0.1:54322/postgres' });
client.connect().then(() => client.query(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename IN ('profiles','contributions')\"))
  .then(r => { console.log(r.rows); return client.end(); });
"
```
Expected: profiles, contributions

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add auth profiles and contributions tables with RLS policies"
```

---

## Task 2: 认证 API 端点

**Files:**
- Create: `src/app/api/auth/register/route.ts`
- Create: `src/app/api/auth/login/route.ts`
- Create: `src/app/api/auth/profile/route.ts`
- Modify: `src/lib/supabase.ts` — 添加带 service_role key 的 admin client

- [ ] **Step 1: 在 supabase.ts 中添加 admin client**

```typescript
// 添加在文件末尾
export const supabaseAdmin = process.env.SUPABASE_SERVICE_ROLE_KEY
  ? createClient(supabaseUrl, process.env.SUPABASE_SERVICE_ROLE_KEY)
  : null
```

- [ ] **Step 2: 创建注册端点**

Create `src/app/api/auth/register/route.ts`:
- POST 接受 { email, password, username, fullName }
- 使用 supabaseAdmin.auth.admin.createUser() 创建用户
- 在 profiles 表插入对应记录
- 返回 user + session

- [ ] **Step 3: 创建登录端点**

Create `src/app/api/auth/login/route.ts`:
- POST 接受 { email, password }
- 使用 supabase.auth.signInWithPassword()
- 返回 session

- [ ] **Step 4: 创建 profile 端点**

Create `src/app/api/auth/profile/route.ts`:
- GET 返回当前用户 profile（从 session 获取 user id）
- PATCH 更新 profile

- [ ] **Step 5: 编写认证 API 测试**

Create `__tests__/api/auth.test.ts`:
- 测试注册（创建用户 + profile）
- 测试登录（返回 session）
- 测试未认证访问被拒绝

- [ ] **Step 6: 运行测试**

```bash
npx vitest run __tests__/api/auth.test.ts
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add auth API endpoints (register/login/profile)"
```

---

## Task 3: 前端认证流程

**Files:**
- Create: `src/components/AuthProvider.tsx`
- Create: `src/app/login/page.tsx`
- Modify: `src/components/Nav.tsx` — 显示登录状态
- Modify: `src/app/layout.tsx` — 包裹 AuthProvider

- [ ] **Step 1: 创建 AuthProvider**

`src/components/AuthProvider.tsx`:
- 使用 Supabase auth context
- 提供 user, profile, signIn, signUp, signOut
- 监听 onAuthStateChange

- [ ] **Step 2: 创建登录/注册页**

`src/app/login/page.tsx`:
- Tab 切换：登录 | 注册
- 登录表单：email + password
- 注册表单：email + password + username + fullName
- 登录后重定向到首页
- 错误提示

- [ ] **Step 3: 更新 Nav**

在 Nav 中添加：
- 未登录时显示"登录"按钮
- 登录后显示用户名 + 下拉菜单（管理/上传/退出）

- [ ] **Step 4: 更新 layout.tsx**

包裹 `<AuthProvider>` 在 body 中

- [ ] **Step 5: 验证构建**

```bash
npx next build
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add auth UI (login/register page, AuthProvider, Nav updates)"
```

---

## Task 4: 势函数上传功能

**Files:**
- Create: `src/components/PotentialForm.tsx`
- Create: `src/app/upload/page.tsx`
- Modify: `src/app/api/potentials/route.ts` — 支持 POST

- [ ] **Step 1: 扩展 API 支持 POST**

在 `src/app/api/potentials/route.ts` 中添加 `POST` 函数：
- 验证用户已登录
- 验证必填字段（name, type, elements）
- 文件上传到 Supabase Storage
- 插入 potentials 表
- 创建 contribution 记录

- [ ] **Step 2: 创建 PotentialForm 组件**

`src/components/PotentialForm.tsx`:
- 表单字段：name, display_name, type (select), elements (tag input), description
- 文件上传区域（拖拽或点击）
- LAMMPS 命令输入
- 引用信息（DOI）
- 提交按钮

- [ ] **Step 3: 创建上传页面**

`src/app/upload/page.tsx`:
- 需要登录（未登录重定向到 /login）
- 使用 PotentialForm 组件
- 提交成功后重定向到详情页

- [ ] **Step 4: 设置 Supabase Storage bucket**

```bash
node -e "
const { createClient } = require('@supabase/supabase-js');
const client = createClient('http://127.0.0.1:54321', '<service-role-key>');
client.storage.createBucket('potentials', { public: true }).then(r => console.log(r));
"
```

- [ ] **Step 5: 编写上传 API 测试**

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add potential upload with file storage and contribution tracking"
```

---

## Task 5: 势函数批量扩展（50+ 条目）

**Files:**
- Create: `scripts/expand-potentials.mjs`

- [ ] **Step 1: 编写批量扩展脚本**

`scripts/expand-potentials.mjs`:
- 从 NIST IPR 的元素页面系统抓取核材料相关势函数
- 覆盖元素：U, Zr, Mo, Nb, O, Fe, He, C, H, Cr, Ni, Si, Ti, Al, Cu
- 每条记录提取：name, type, elements, citation/DOI, description
- 插入 potentials 表（跳过已存在的 name）
- 目标：从 10 条扩展到 50+ 条

- [ ] **Step 2: 运行扩展脚本**

```bash
node scripts/expand-potentials.mjs
```

- [ ] **Step 3: 验证数据量**

```bash
node -e "
const pg = require('pg');
const client = new pg.Client({ connectionString: process.env.DATABASE_URL || 'postgresql://postgres:postgres@127.0.0.1:54322/postgres' });
client.connect().then(() => client.query('SELECT count(*) FROM potentials'))
  .then(r => { console.log('Total potentials:', r.rows[0].count); return client.end(); });
"
```
Expected: 50+

- [ ] **Step 4: 更新种子数据完整性测试**

在 `__tests__/e2e/seed-data.test.ts` 中：
- 更新 totalPotentials 阈值从 10 到 50+
- 添加新体系的覆盖检查

- [ ] **Step 5: 运行全部测试**

```bash
npx vitest run
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: expand potential library to 50+ entries via NIST IPR batch import"
```

---

## Task 6: 管理后台

**Files:**
- Create: `src/app/admin/page.tsx`
- Modify: `src/components/Nav.tsx` — admin 入口

- [ ] **Step 1: 创建管理后台页面**

`src/app/admin/page.tsx`:
- 需要管理员角色（非管理员重定向）
- 统计概览（势函数数、用户数、贡献数）
- 待审核贡献列表（approve/reject）
- 势函数管理（编辑/删除/标记 deprecated）

- [ ] **Step 2: 添加 admin API 端点**

Create `src/app/api/admin/stats/route.ts`:
- 返回平台统计（用户数、贡献数、待审核数）
- 需要管理员权限

Create `src/app/api/admin/contributions/route.ts`:
- GET：列出待审核贡献
- PATCH：审核通过/拒绝

- [ ] **Step 3: 更新 Nav**

添加管理入口（仅 admin 角色可见）

- [ ] **Step 4: 验证构建**

```bash
npx next build
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add admin dashboard with contribution review"
```

---

## Task 7: Phase 2 集成测试 + 最终验证

**Files:**
- Create: `__tests__/e2e/phase2-smoke.test.ts`

- [ ] **Step 1: 编写 Phase 2 冒烟测试**

验证：
- 注册 → 登录 → 获取 profile 完整流程
- 认证用户可以创建势函数
- 非认证用户无法创建势函数
- 数据量 ≥ 50

- [ ] **Step 2: 运行全部测试**

```bash
npx vitest run
```

- [ ] **Step 3: 运行构建**

```bash
npx next build
```

- [ ] **Step 4: 手动验证完整流程**

```
1. 访问首页 → 浏览势函数（50+条目）
2. 注册新用户 → 登录
3. 上传势函数 → 查看详情页
4. 管理后台 → 审核贡献
```

- [ ] **Step 5: 使用 finishing-a-development-branch 合并**

- [ ] **Step 6: 更新 README 路线图**

将二期标记为完成，更新截图。

- [ ] **Step 7: Commit + Push**

```bash
git add -A
git commit -m "test: Phase 2 integration tests + final verification"
git push
```
