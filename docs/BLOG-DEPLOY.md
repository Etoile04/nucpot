# NucPot 博客系统部署指南

> 面向 CTO Agent 编写的完整部署文档，涵盖本地开发、Vercel 生产部署、博客内容管理等全部流程。

---

## 1. 博客系统架构概览

```
content/blog/*.md          ← Markdown 文章（Git 版本控制，提交即上线）
    │
    ▼ gray-matter 解析 frontmatter
src/lib/blog.ts             ← 文章读取、解析、排序
    │
    ▼
src/app/blog/page.tsx      ← 博客列表页 (/blog)
src/app/blog/[slug]/page.tsx ← 文章详情页 (/blog/:slug)
    │
    ▼
src/app/sitemap.ts          ← 自动将文章加入 sitemap.xml
src/app/robots.ts           ← SEO robots 配置
```

**核心特点：**
- **纯静态生成**（SSG）：`generateStaticParams` 预渲染所有文章为 HTML，零数据库依赖
- **Git-based CMS**：新增文章 = 新增 `.md` 文件 + `git push`，无需登录后台
- **Next.js 16 standalone**：Docker 部署用 `output: "standalone"` 模式

---

## 2. 项目仓库信息

| 项目 | 值 |
|------|-----|
| **Git Remote** | `https://github.com/Etoile04/nucpot.git` |
| **主分支** | `main` |
| **项目路径** | `/Users/lwj04/Projects/nucpot` |
| **域名** | `nucpot.dpdns.org` |
| **托管服务** | **Vercel**（自动部署） |

### 目录结构（实际项目，非 apps/ 结构）

```
nucpot/
├── content/blog/              ← 博客 Markdown 文章
│   └── zirconium-alloy-properties.md
├── src/
│   ├── app/
│   │   ├── page.tsx           ← 首页
│   │   ├── blog/
│   │   │   ├── page.tsx       ← 博客列表页
│   │   │   └── [slug]/page.tsx ← 文章详情页
│   │   └── ...
│   ├── components/            ← 共享组件
│   │   └── Footer.tsx
│   └── lib/
│       └── blog.ts            ← 博客工具库（文件读取 + 解析）
├── Dockerfile.web             ← Web 容器构建
├── docker-compose.yml         ← 完整服务编排
├── next.config.ts             ← output: "standalone"
├── package.json               ← npm，Next.js 16.2.6
└── .github/workflows/         ← CI（构建 + 测试）
```

> ⚠️ **重要**：项目使用的是 **`src/` 扁平结构**，不是 `apps/web/src/` monorepo 结构。如果你的代码在 `apps/web/src/` 下，那是错误的目录布局。

---

## 3. CI/CD 与自动部署

### 3.1 Vercel 自动部署（主站）

nucpot.dpdns.org 托管在 **Vercel** 上，配置了自动部署：

```
git push origin main  →  GitHub 触发 push event  →  Vercel 自动构建  →  约 1-2 分钟上线
```

**不需要手动部署步骤。** push 到 main 即触发部署。

### 3.2 GitHub Actions CI

`.github/workflows/` 中有 CI workflow（push/main + PR），执行：

```
npm ci → eslint → tsc --noEmit → npm run build → npm test
```

CI 失败不会阻止 Vercel 部署（Vercel 有自己的构建流程），但应在 push 前本地通过。

### 3.3 Vercel 环境变量

在 Vercel Dashboard 中已配置（Production 环境）：

| 变量 | 值 |
|------|-----|
| `NEXT_PUBLIC_SUPABASE_URL` | `https://gzhiqyopzlmnkdzammhx.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase publishable key |
| `NEXT_PUBLIC_AUTOCV_API_URL` | `https://verify.nucpot.dpdns.org` |

> 博客功能本身不需要环境变量——它直接读 `content/blog/` 的本地文件。

---

## 4. 关于 CTO Agent 当前代码的处理

CTO Agent 在 commit `4b71bb6` 中将博客代码提交到了 `apps/web/src/` 目录结构，但 **nucpot 项目使用 `src/` 结构**。

### 需要的迁移步骤

将 CTO Agent 的代码从 `apps/web/` 结构映射到项目实际结构：

| CTO Agent 路径 | 实际项目路径 | 说明 |
|----------------|-------------|------|
| `apps/web/src/app/page.tsx` | `src/app/page.tsx` | 首页 |
| `apps/web/src/app/blog/page.tsx` | `src/app/blog/page.tsx` | 博客列表页 |
| `apps/web/src/app/blog/[slug]/page.tsx` | `src/app/blog/[slug]/page.tsx` | 文章详情页 |
| `apps/web/src/app/blog/blog.css` | `src/app/blog/blog.css` | 博客样式 |
| `apps/web/src/components/blog/BlogCard.tsx` | `src/components/blog/BlogCard.tsx` | 博客卡片组件 |
| `apps/web/src/components/blog/BlogNavigation.tsx` | `src/components/blog/BlogNavigation.tsx` | 博客导航 |
| `apps/web/src/app/admin/blog/` | `src/app/admin/blog/` | 博客管理后台 |

### 迁移检查清单

- [ ] 将组件文件移动到 `src/components/blog/` 目录
- [ ] 将页面文件移动到 `src/app/blog/` 目录（覆盖现有文件或合并）
- [ ] 将 admin 页面移动到 `src/app/admin/blog/` 目录
- [ ] 将 CSS 文件移动到 `src/app/blog/blog.css`
- [ ] 更新所有 import 路径（`@/components/blog/...` 应保持不变）
- [ ] 确保 `src/lib/blog.ts` 的 `getAllPosts()` 和 `getPostBySlug()` 逻辑与你的改动兼容
- [ ] 确保 `content/blog/` 目录的 Markdown 文件 frontmatter 格式匹配你的组件预期字段
- [ ] 本地 `npm run build` 通过后才能 push
- [ ] 更新 `src/app/sitemap.ts`（如果博客字段有变化）

### Git Remote 配置

CTO Agent 报告 `git remote -v` 返回空，说明它不在 nucpot 主仓库目录中工作。

**正确做法：** 在 nucpot 主仓库中工作：

```bash
cd /Users/lwj04/Projects/nucpot

# 确认 remote 已配置
git remote -v
# 应输出：
# origin  https://github.com/Etoile04/nucpot.git (fetch)
# origin  https://github.com/Etoile04/nucpot.git (push)
```

如果你在一个独立的副本目录中工作，需要将更改 cherry-pick 或手动复制到主仓库：

```bash
# 方式一：直接在主仓库中编辑文件（推荐）
cd /Users/lwj04/Projects/nucpot

# 方式二：从你的副本中复制文件
# 先确定你的工作目录，然后复制文件到主仓库对应位置
cp /your-work-dir/apps/web/src/app/blog/page.tsx /Users/lwj04/Projects/nucpot/src/app/blog/page.tsx
# ... 复制其他文件

# 方式三：如果已 commit 到某个 git 仓库，可以 cherry-pick
cd /Users/lwj04/Projects/nucpot
git remote add cto-work /path/to/your/repo
git fetch cto-work
git cherry-pick <commit-hash>
# 然后修复路径冲突
```

---

## 5. 部署步骤（完整流程）

### 首次配置 remote

```bash
cd /Users/lwj04/Projects/nucpot
git remote add origin https://github.com/Etoile04/nucpot.git
# 如果已有 origin 但 URL 错误：
git remote set-url origin https://github.com/Etoile04/nucpot.git
```

### 发布更改到生产环境

```bash
# 1. 确保在 main 分支
git checkout main

# 2. 确保 up to date
git pull origin main

# 3. 将 CTO Agent 的改动合并到正确位置（见第 4 节迁移步骤）

# 4. 本地验证
npm run build
npm test

# 5. 提交并推送
git add -A
git commit -m "feat: blog redesign - visual upgrade + admin interface"
git push origin main

# 6. Vercel 自动部署，约 1-2 分钟后访问
#    https://nucpot.dpdns.org/blog
```

### 不需要手动部署

- ✅ Vercel 监听 GitHub `main` 分支，push 后自动构建部署
- ✅ 不需要 SSH 到服务器
- ✅ 不需要手动运行 `vercel` CLI（除非做自定义部署）
- ❌ 不需要 `.github/workflows/` 中的部署 workflow（Vercel 自身处理）

---

## 6. 本地开发

### 6.1 环境准备

```bash
cd /Users/lwj04/Projects/nucpot
npm install

# 最小 .env.local（博客功能不需要 Supabase，但网站其他部分需要）
cp .env.example .env.local
# 编辑填入 Supabase URL 和 key
```

### 6.2 启动开发服务器

```bash
npm run dev
# 访问 http://localhost:3000/blog
```

### 6.3 新增博客文章

在 `content/blog/` 下创建 `.md` 文件：

```markdown
---
title: "文章标题"
date: "2026-06-13"
description: "文章摘要"
author: "作者名"
tags: ["标签1", "标签2"]
ogImage: "https://example.com/cover.jpg"   # 可选
---

# 正文标题

Markdown 正文内容...
```

### 6.4 图片管理

**方式一：public 目录（推荐）**

```
public/blog/image.png    → 引用路径: /blog/image.png
```

**方式二：外部 URL**

```markdown
![描述](https://example.com/image.png)
```

---

## 7. Docker 部署（备用/ThinkStation）

### 构建

```bash
docker build -f Dockerfile.web -t nucpot-web .
```

### 运行

```bash
docker run -d --name nucpot-web -p 3000:3000 -e NODE_ENV=production nucpot-web
```

### Docker Compose（完整服务栈）

```bash
docker compose up -d
# 启动：web(3000) + verify-api(8000) + verify-worker + redis(6379)
```

### ThinkStation 更新

```bash
ssh z203@100.70.30.21
cd ~/nucpot
git pull origin main
docker compose up -d --build web
```

---

## 8. 域名与 DNS

| 域名 | 指向 | 服务 |
|------|------|------|
| `nucpot.dpdns.org` | Vercel | Next.js 主站（含博客） |
| `verify.nucpot.dpdns.org` | Cloudflare Tunnel → ThinkStation | 验证 API |

---

## 9. SEO 自动配置

- **sitemap.xml**：`src/app/sitemap.ts` 自动包含所有博客文章
- **robots.txt**：`src/app/robots.ts` 允许爬取
- **Open Graph**：每篇文章自动生成 OG meta
- 无需额外配置

---

## 10. 常见问题（FAQ）

### Q1: 新文章推送后 Vercel 没有更新？
1. 检查 Vercel Dashboard → Deployments，确认构建是否触发
2. 确认 push 到了 `main` 分支
3. 查看 Vercel 构建日志是否有错误
4. 确认 `.md` 文件在 `content/blog/` 下，frontmatter 格式正确
5. 清除浏览器缓存（Ctrl+Shift+R）

### Q2: 本地 `npm run dev` 看不到新文章？
- 确认文件在 `content/blog/` 目录下
- 确认扩展名为 `.md`
- 确认 frontmatter 有 `title` 和 `date` 字段
- 开发服务器自动热重载，无需重启

### Q3: 构建报错 `Module not found`？
```bash
rm -rf node_modules package-lock.json
npm install
```

### Q4: Vercel 构建成功但博客 404？
- `date` 字段格式必须是 `"YYYY-MM-DD"`
- 文件名仅用小写字母、数字、短横线
- 检查 Vercel 环境变量

### Q5: Docker 构建失败？
确认 `pnpm-lock.yaml` 存在，或修改 Dockerfile 使用 npm。

### Q6: 如何编辑/删除已发布文章？
```bash
# 编辑
vim content/blog/article.md
git add content/blog/article.md && git commit -m "blog: update" && git push

# 删除
git rm content/blog/article.md
git commit -m "blog: remove" && git push
```

### Q7: Markdown HTML 标签不生效？
`react-markdown v10` 默认剥离 HTML。需要 `rehype-raw` 插件。

### Q8: CTO Agent 的代码在 `apps/web/` 而不是 `src/`？
这是**目录结构不匹配**。nucpot 项目使用 `src/` 扁平结构。参考第 4 节迁移步骤将代码放到正确位置。

### Q9: CI 测试失败？
```bash
npm test                    # 查看测试输出
npx tsc --noEmit           # 类型检查
npm run lint               # ESLint
```

### Q10: 如何查看部署状态？
- Vercel Dashboard: https://vercel.com/dashboard
- 查看最新 deployment 的构建日志和状态

---

## 11. 快速参考

### 关键命令

```bash
# 本地开发
npm run dev

# 构建验证
npm run build && npm test

# 发布到生产
git push origin main

# ThinkStation Docker 更新
ssh z203@100.70.30.21 "cd ~/nucpot && git pull && docker compose up -d --build web"
```

### 关键路径

| 路径 | 说明 |
|------|------|
| `content/blog/` | 博客文章源文件 |
| `src/lib/blog.ts` | 文章解析工具库 |
| `src/app/blog/page.tsx` | 博客列表页 |
| `src/app/blog/[slug]/page.tsx` | 文章详情页 |
| `src/app/blog/blog.css` | 博客样式 |
| `src/components/blog/` | 博客组件目录 |
| `src/app/admin/blog/` | 博客管理后台 |
| `public/blog/` | 博客图片资源 |
| `Dockerfile.web` | Web 容器构建 |
| `docker-compose.yml` | 完整服务编排 |
| `next.config.ts` | `output: "standalone"` |

### Git Remote

```
origin  https://github.com/Etoile04/nucpot.git
```

### 域名

```
nucpot.dpdns.org       → Vercel（Next.js 主站）
verify.nucpot.dpdns.org → Cloudflare Tunnel → ThinkStation（验证 API）
```

---

*最后更新：2026-06-13 | 维护者：NucPot 团队*
