# NucPot - 核材料势函数库

面向核燃料、包壳和结构材料的原子间势函数开放平台 MVP。

## 快速开始

### 前置要求

- Node.js 18+
- Docker Desktop（用于本地 Supabase）

### 1. 启动本地数据库

```bash
# 启动 Docker Desktop 后执行
supabase start
```

启动后会显示本地连接信息，将 URL 和 Key 更新到 `.env.local`。

### 2. 初始化 Schema + 种子数据

```bash
supabase db reset
# 这会执行 supabase/schema.sql（建表 + 10 条种子数据）
```

### 3. 启动开发服务器

```bash
npm run dev
# 打开 http://localhost:3000
```

## 项目结构

```
src/
├── app/
│   ├── page.tsx              # 首页
│   ├── browse/page.tsx       # 势函数浏览
│   ├── potential/[id]/       # 势函数详情
│   └── api/
│       ├── potentials/        # 势函数 CRUD API
│       └── stats/             # 统计 API
├── lib/
│   └── supabase.ts           # Supabase 客户端
supabase/
├── schema.sql                # 数据库 Schema + 种子数据
└── config.toml               # Supabase 本地配置
```

## 技术栈

- **前端**: Next.js 16 (App Router) + Tailwind CSS
- **数据库**: Supabase (PostgreSQL) + JSONB + 全文检索
- **部署**: Vercel (二期) / 本地开发
