# NucPot 部署指南 — Supabase Cloud + Vercel

> 目标：半天内上线，零成本起步

---

## 前置准备

- [ ] GitHub 账号（已有：Etoile04）
- [ ] Supabase 账号（https://supabase.com，可用 GitHub 登录）
- [ ] Vercel 账号（https://vercel.com，可用 GitHub 登录）

---

## Step 1：Supabase Cloud 创建项目

1. 登录 https://supabase.com → **New Project**
2. 填写：
   - **Name**: `nucpot`
   - **Database Password**: 生成一个强密码，**保存好**
   - **Region**: 选离国内最近的（Singapore / Tokyo）
3. 等待项目初始化（~2 分钟）

### 1.1 获取连接信息

项目创建后，进入 **Settings → Database**：
- 记录 **Connection string**（URI 格式），形如：
  ```
  postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres
  ```

进入 **Settings → API**：
- 记录以下三个值：
  ```
  Project URL        →  NEXT_PUBLIC_SUPABASE_URL
  anon public key    →  NEXT_PUBLIC_SUPABASE_ANON_KEY
  service_role key   →  SUPABASE_SERVICE_ROLE_KEY（⚠️ 保密）
  ```

### 1.2 执行 Schema

进入 **SQL Editor**，依次执行：

**① 基础表（schema.sql）**
```sql
-- 复制 supabase/schema.sql 的全部内容粘贴执行
```

**② 认证迁移（migration）**
```sql
-- 复制 supabase/migrations/001_auth_and_profiles.sql 的全部内容粘贴执行
```

### 1.3 灌入种子数据

在本地执行（需要 Node.js + pg 包）：

```bash
cd ~/projects/nucpot

# 设置 Cloud 数据库连接串
export DATABASE_URL="postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres"

# 用 Node 直接连接灌数据（seed 脚本会自动创建表+数据）
# ⚠️ seed-db.mjs 会先 DROP TABLE，如果 Step 1.2 已经建了表，需要确认或跳过建表部分
# 建议方式：用 expand-potentials.mjs 的数据部分单独插入

# 方式 A：用 SQL Editor 直接粘贴 schema.sql 末尾的 INSERT 语句
# 方式 B：用脚本
node scripts/seed-db.mjs
node scripts/expand-potentials.mjs
```

**推荐方式 A**：在 Supabase SQL Editor 中直接执行 schema.sql（含种子 INSERT），更简单可控。

### 1.4 配置 Auth

进入 **Authentication → Providers**：
- 确保 **Email** 已启用
- 可选：关闭 "Confirm email"（开发阶段方便测试）

进入 **Authentication → URL Configuration**：
- **Site URL**: `https://nucpot.vercel.app`（或自定义域名）
- **Redirect URLs**: 添加 `https://nucpot.vercel.app/**`

---

## Step 2：Vercel 部署前端

### 2.1 导入项目

1. 登录 https://vercel.com → **Add New → Project**
2. 选择 **Import Git Repository** → `Etoile04/nucpot`
3. 配置：
   - **Framework Preset**: Next.js（自动检测）
   - **Root Directory**: `.`（默认）
   - **Build Command**: `npm run build`（默认）
   - **Output Directory**: `.next`（默认）

### 2.2 设置环境变量

在 Vercel 项目 **Settings → Environment Variables** 中添加：

| Key | Value | 环境 |
|-----|-------|------|
| `NEXT_PUBLIC_SUPABASE_URL` | `https://[ref].supabase.co` | Production, Preview, Dev |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `eyJ...` | Production, Preview, Dev |
| `SUPABASE_SERVICE_ROLE_KEY` | `eyJ...` | Production, Preview, Dev |

### 2.3 部署

点击 **Deploy**，等待构建完成（~2 分钟）。

部署成功后获得：
- **预览 URL**: `https://nucpot-[hash].vercel.app`
- **生产 URL**: `https://nucpot.vercel.app`

---

## Step 3：验证

### 3.1 功能检查清单

- [ ] 首页加载正常，显示势函数统计
- [ ] 浏览页显示 50 个势函数
- [ ] 搜索功能正常（按元素/类型/关键词）
- [ ] 势函数详情页显示完整信息
- [ ] 注册/登录功能正常
- [ ] 上传功能（需登录）
- [ ] 管理后台（需 admin 角色）

### 3.2 创建 Admin 用户

1. 在网站上注册一个账号
2. 在 Supabase **SQL Editor** 中提升为 admin：
   ```sql
   UPDATE profiles SET role = 'admin' WHERE username = '你的用户名';
   ```

---

## Step 4：域名绑定（可选）

如果有自定义域名（如 `nucpot.cn`）：

1. Vercel → **Settings → Domains** → 添加域名
2. 按提示在域名 DNS 添加 CNAME 记录指向 `cname.vercel-dns.com`
3. 等待 SSL 证书自动签发

同时更新 Supabase Auth 的 Site URL 和 Redirect URLs。

---

## 成本估算

| 服务 | 免费额度 | 备注 |
|------|---------|------|
| Supabase | 500MB DB + 1GB Storage + 50K Auth MAU | 50 个势函数远低于上限 |
| Vercel | 100GB 带宽/月 + Serverless 函数 | 学术项目流量不会超 |
| **总计** | **¥0/月** | — |

超出免费额度后再考虑升级。

---

## 常见问题

### Q: `npm run build` 失败？
检查环境变量是否完整设置在 Vercel 中。

### Q: 数据库连接超时？
Supabase Free tier 有连接池限制，确认使用的是 **Pooler URL**（端口 6543）而非直连 URL（端口 5432）。

### Q: Auth 登录后无反应？
检查 Supabase Authentication → URL Configuration 中 Site URL 是否匹配 Vercel 域名。

### Q: 需要添加更多势函数？
本地运行 `node scripts/expand-potentials.mjs` 或在管理后台手动上传。
