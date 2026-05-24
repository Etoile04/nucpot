# NucPot 使用教程

本文档介绍如何部署 NucPot 并使用各项功能。

---

## 📦 部署指南

### 前置要求

| 工具 | 版本 | 用途 |
|------|------|------|
| Node.js | 18+ | 运行 Next.js 前端 |
| npm | 9+ | 包管理 |
| Docker Desktop | 最新 | 运行本地 Supabase |
| Supabase CLI | 2.x+ | 数据库管理 |

> **提示：** 推荐使用 macOS / Linux。Windows 用户建议使用 WSL2。

### 第 1 步：克隆仓库

```bash
git clone https://github.com/Etoile04/nucpot.git
cd nucpot
npm install
```

### 第 2 步：启动本地 Supabase

确保 Docker Desktop 已启动，然后：

```bash
# 安装 Supabase CLI（如果未安装）
npx supabase --version

# 启动本地 Supabase（首次会拉取镜像，约 2-5 分钟）
npx supabase init   # 仅首次需要
npx supabase start
```

启动后会输出连接信息，记下 `ANON_KEY` 和 `API URL`：

```
         API URL: http://127.0.0.1:54321
          DB URL: postgresql://postgres:postgres@127.0.0.1:54322/postgres
      Studio URL: http://127.0.0.1:54323
    anon key: eyJhbG...（很长的一串）
```

### 第 3 步：初始化数据库

```bash
# 执行 Schema + 种子数据（10 个基础势函数）
node scripts/seed-db.mjs
```

然后执行 Phase 2 迁移（认证表 + 权限）：

```bash
# 连接数据库执行迁移
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  -f supabase/migrations/001_auth_and_profiles.sql
```

> 如果没有 `psql`，可以用 Node.js 执行：
> ```bash
> node -e "
>   const pg = require('pg');
>   const c = new pg.Client('postgresql://postgres:postgres@127.0.0.1:54322/postgres');
>   c.connect().then(async () => {
>     const sql = require('fs').readFileSync('supabase/migrations/001_auth_and_profiles.sql', 'utf8');
>     await c.query(sql);
>     console.log('✅ Migration applied');
>     await c.end();
>   });
> "
> ```

再扩展到 50+ 势函数：

```bash
node scripts/expand-potentials.mjs
```

### 第 4 步：配置环境变量

创建 `.env.local` 文件（已在 `.gitignore` 中，不会被提交）：

```bash
# Supabase 本地开发配置
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=<粘贴第 2 步获得的 anon key>
SUPABASE_SERVICE_ROLE_KEY=<需要生成，见下方>
```

**获取 Service Role Key：**

Service Role Key 是用于管理操作的 JWT token，需要用 Supabase 的 JWT Secret 签发。

```bash
# 方法 1：从 Supabase Studio 获取
# 打开 http://127.0.0.1:54323 → Settings → API → service_role key

# 方法 2：用 Python 生成（本地默认 secret）
pip install pyjwt
python3 -c "
import jwt, time
secret = 'super-secret-jwt-token-with-at-least-32-characters-long'
token = jwt.sign({'role': 'service_role', 'iss': 'supabase', 'exp': int(time.time()) + 315360000}, secret, algorithm='HS256')
print(token)
"
```

将输出的 token 粘贴为 `SUPABASE_SERVICE_ROLE_KEY`。

### 第 5 步：启动开发服务器

```bash
npm run dev
# 打开 http://localhost:3000
```

看到首页即部署成功 🎉

### 第 6 步：创建管理员账号

数据库初始化后没有用户，需要手动创建第一个管理员：

```bash
node -e "
const { createClient } = require('@supabase/supabase-js');
const admin = createClient('http://127.0.0.1:54321', '<你的 SERVICE_ROLE_KEY>');

(async () => {
  // 创建用户
  const { data, error } = await admin.auth.admin.createUser({
    email: 'admin@nucpot.local',
    password: 'admin123',
    email_confirm: true,
  });
  if (error) { console.error(error.message); return; }
  
  // 设置管理员角色
  await admin.from('profiles').upsert({
    id: data.user.id,
    username: 'admin',
    full_name: 'Administrator',
    email: 'admin@nucpot.local',
    role: 'admin',
  });
  
  console.log('✅ Admin created: admin@nucpot.local / admin123');
  console.log('   User ID:', data.user.id);
})();
"
```

> ⚠️ **生产环境请使用强密码！** 这里仅作演示。

---

## 📖 功能使用指南

### 1. 浏览势函数

**访问路径：** 首页 → 「浏览全部」或导航栏 → 「浏览」

- 默认显示所有势函数，按更新时间排序
- 左侧筛选器可按 **类型**（EAM / MEAM / Buckingham 等）和 **元素** 筛选
- 点击卡片进入详情页

### 2. 高级检索

**访问路径：** 导航栏 → 「检索」

检索页提供核材料专用筛选维度：

| 筛选器 | 说明 | 示例 |
|--------|------|------|
| 元素组合 | 输入元素符号，逗号分隔 | `U,Zr` 查找含 U 或 Zr 的势函数 |
| 势函数类型 | 下拉选择 | EAM / MEAM / Buckingham / Tersoff / ML |
| 温度范围 | 滑块选择 | 300-2000 K |
| 辐照相关 | 勾选框 | 仅显示有辐照数据的势函数 |
| 缺陷数据 | 勾选框 | 仅显示含缺陷计算数据的势函数 |
| 液相数据 | 勾选框 | 仅显示含液相参数的势函数 |

### 3. 查看势函数详情

**访问路径：** 浏览/检索页 → 点击势函数卡片

详情页包含四个标签页：

- **概述** — 基本信息、适用范围、开发者信息
- **验证性质** — 已验证的物理性质（晶格常数、弹性常数、空位形成能等）
- **引用信息** — DOI 链接、论文引用
- **使用指南** — LAMMPS 命令一键复制

### 4. 注册与登录

**访问路径：** 导航栏 → 「登录」

#### 注册新账号

1. 点击「注册」标签
2. 填写：
   - **邮箱** — 用于登录
   - **密码** — 至少 6 位
   - **用户名** — 显示名称，唯一
   - **姓名** — 可选，真实姓名
3. 点击「注册」
4. 自动登录并跳转首页

#### 登录

1. 在「登录」标签输入邮箱和密码
2. 登录后导航栏显示用户名和下拉菜单

### 5. 上传势函数

**访问路径：** 导航栏 → 「上传势函数」（需登录）

上传表单字段说明：

| 字段 | 必填 | 说明 |
|------|:----:|------|
| 势函数名称 | ✅ | 唯一标识符，如 `EAM_FeCr_YourName_2026` |
| 显示名称 | | 用于展示的友好名称 |
| 类型 | ✅ | EAM / MEAM / Buckingham / Tersoff / AIREBO / LJ / 其他 |
| 子类型 | | 如 `eam/alloy`、`eam/cd` |
| 格式 | | LAMMPS / DYNAMO 等 |
| 元素 | ✅ | 逗号分隔，如 `Fe, Cr` |
| 体系名称 | ✅ | 如 `Fe-Cr alloy` |
| 体系标签 | | 如 `结构材料, 不锈钢` |
| 描述 | ✅ | 势函数的详细描述 |
| 温度范围 | | 适用温度区间，如 `300-1500` |
| 相 | | 适用的晶体相，如 `BCC, FCC` |
| LAMMPS pair_style | | 如 `eam/alloy` |
| LAMMPS pair_coeff | | 如 `* * FeCr.eam.alloy Fe Cr` |
| 标签 | | 逗号分隔的搜索标签 |
| 参考文献 DOI | | 相关论文 DOI |

**上传流程：**

```
填写表单 → 提交 → 状态变为「待审核」 → 管理员审核 → 「已发布」
```

上传后势函数状态为 `pending`（待审核），需管理员在后台审核通过后才会正式发布。

### 6. 管理后台

**访问路径：** 导航栏 → 「管理后台」（仅 admin 角色可见）

管理后台包含两个功能区：

#### 统计概览

- 势函数总数和按类型分布
- 势函数来源分布（NIST IPR / user_contributed）
- 注册用户数和角色分布
- 待审核贡献数

#### 贡献审核

- 查看所有待审核的用户上传
- 每条贡献显示：贡献者、势函数信息、提交时间
- 操作：
  - ✅ **通过** — 势函数状态变为 `approved`，正式发布
  - ❌ **拒绝** — 势函数保留但标记为 `rejected`

---

## 🔧 常见问题

### Q: `supabase start` 失败？

确保 Docker Desktop 已启动并运行。检查端口 54321-54324 未被占用：

```bash
lsof -i :54321
# 如果被占用，可以停止占用进程或修改 supabase 端口配置
```

### Q: `seed-db.mjs` 报连接错误？

确认 Supabase 正在运行：

```bash
npx supabase status
```

如果显示 `Stopped`，执行 `npx supabase start`。

### Q: 注册报 `permission denied`？

可能是数据库权限未配置。执行迁移文件中的 GRANT 语句：

```bash
node -e "
const pg = require('pg');
const c = new pg.Client('postgresql://postgres:postgres@127.0.0.1:54322/postgres');
c.connect().then(async () => {
  await c.query('GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role');
  await c.query('GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon');
  await c.query('GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO authenticated');
  await c.query('GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role');
  await c.query('GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO authenticated');
  await c.query('GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role');
  console.log('✅ Permissions granted');
  await c.end();
});
"
```

### Q: 上传的势函数看不到？

用户上传的势函数状态为 `pending`（待审核），需要管理员在后台审核通过后才会出现在浏览页。

### Q: 如何修改用户为管理员？

```bash
node -e "
const pg = require('pg');
const c = new pg.Client('postgresql://postgres:postgres@127.0.0.1:54322/postgres');
c.connect().then(async () => {
  await c.query(\"UPDATE profiles SET role = 'admin' WHERE username = '\$1'\", ['目标用户名']);
  console.log('✅ Done');
  await c.end();
});
"
```

### Q: 如何重置数据库？

```bash
npx supabase db reset
# 然后重新执行第 3 步
node scripts/seed-db.mjs
node scripts/expand-potentials.mjs
```

---

## 🏗️ 项目结构速查

```
nucpot/
├── src/app/                    # Next.js 页面和 API
│   ├── page.tsx                # 首页
│   ├── browse/                 # 浏览页
│   ├── search/                 # 检索页
│   ├── potential/[id]/         # 势函数详情页
│   ├── login/                  # 登录/注册页
│   ├── upload/                 # 势函数上传页
│   ├── admin/                  # 管理后台
│   └── api/                    # API 端点
│       ├── auth/               # 认证 API (register/login/profile)
│       ├── potentials/         # 势函数 API (list/detail/upload)
│       ├── admin/              # 管理 API (stats/contributions)
│       └── stats/              # 公共统计 API
├── src/components/             # React 组件
│   ├── Nav.tsx                 # 导航栏（含用户状态）
│   └── AuthProvider.tsx        # 认证上下文
├── src/lib/                    # 工具库
│   ├── supabase.ts             # Supabase 客户端（含 admin）
│   └── types.ts                # TypeScript 类型
├── supabase/
│   ├── schema.sql              # MVP Schema
│   ├── migrations/             # Phase 2+ 迁移
│   └── config.toml             # Supabase 配置
├── scripts/
│   ├── seed-db.mjs             # 初始化 Schema + 10 条种子数据
│   └── expand-potentials.mjs   # 批量扩展到 50+
├── __tests__/                  # 测试文件
├── docs/screenshots/           # UI 截图
└── .env.local                  # 环境变量（不提交）
```

---

## 📡 API 速查

### 公共 API（无需认证）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/potentials` | 势函数列表（支持分页/筛选） |
| GET | `/api/potentials/[id]` | 势函数详情 |
| GET | `/api/stats` | 统计数据 |

**查询参数（`/api/potentials`）：**

```
?type=EAM                    # 按类型筛选
&elements=U,Zr               # 按元素筛选（OR 逻辑）
&tags=核材料,金属燃料          # 按标签筛选
&irradiationRelevant=true     # 仅辐照相关
&hasDefectData=true           # 含缺陷数据
&hasLiquidPhase=true          # 含液相数据
&search=关键词                 # 全文检索
&page=1&limit=20              # 分页
```

### 认证 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录 |
| GET | `/api/auth/profile` | 获取当前用户信息 |

### 受保护 API（需 Bearer Token）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/potentials/upload` | 上传势函数 |

### 管理 API（需 admin 角色）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/stats` | 管理统计 |
| GET | `/api/admin/contributions` | 贡献列表 |
| PATCH | `/api/admin/contributions` | 审核贡献 |

---

*最后更新：2026-05-24*
