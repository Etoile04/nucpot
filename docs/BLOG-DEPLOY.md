# NucPot 博客系统部署指南

> 本文档是博客子系统数据流、环境变量与部署要点的权威说明。
> 整体生产部署流程（镜像构建、compose 启动、审批门禁、回滚）以
> [docs/runbooks/prod-deploy.md](./runbooks/prod-deploy.md) 为准，此处不重复。

---

## 1. 博客系统架构概览（DB 驱动）

```
/admin/blog（管理后台，apps/web）
    │  发布 / 编辑 / 下线
    ▼
blog_posts 表（数据库，单一事实源）
    │
    ▼ GET /api/v1/blog/public[/{slug}]
apps/api …/api/v1/blog.py        ← 公开博客 API（无需认证）
    │
    ▼ SSR fetch（绝对地址，ISR 60s）
apps/web/src/lib/blog/public-posts.ts
    │
    ▼
apps/web/src/app/blog/page.tsx           ← 博客列表页 (/blog)
apps/web/src/app/blog/[slug]/page.tsx    ← 文章详情页 (/blog/:slug)
```

**核心特点：**

- **数据库驱动**：文章由管理后台写入数据库，不依赖 Markdown 文件上线（NFM-4085 之前基于 `content/blog/*.md` 的纯 SSG 方案已废弃）。
- **ISR 增量渲染**：页面 `revalidate = 60`，发布后约 1 分钟内出现在公开页面，无需重新构建。
- **构建期 / 故障回退**：`next build` 期间 API 不可达，以及运行期 API 调用失败时，回退到文件系统种子文章（`apps/web/content/blog/`，可用 `BLOG_CONTENT_DIR` 覆盖）——仅作兜底，不是发布渠道。

---

## 2. 代码位置（本 monorepo）

| 路径 | 说明 |
|------|------|
| `apps/web/src/app/blog/page.tsx` | 博客列表页（`/blog`） |
| `apps/web/src/app/blog/[slug]/page.tsx` | 文章详情页 |
| `apps/web/src/lib/blog/public-posts.ts` | 公开文章数据层（API fetch + 种子回退） |
| `apps/web/src/lib/blog/posts.ts` | 文件系统种子文章解析（legacy） |
| `apps/web/src/app/admin/blog/` | 博客管理后台 |
| `apps/api/src/nfm_db/api/v1/blog.py` | 博客 API（含 `/blog/public` 公开端点） |
| `apps/web/content/blog/` | 种子 Markdown 文章（兜底数据源） |

---

## 3. 环境变量（关键）

| 变量 | 作用 | 生产值 |
|------|------|--------|
| `API_SERVER_URL` | 博客 SSR fetch 的**绝对** API 基地址 | `http://nucpot-prod-api:8000`（docker-compose.prod.yml 中 web 服务已设置） |
| `BLOG_CONTENT_DIR` | （可选）覆盖种子文章目录 | 未设置 |

> **NFM-4940 要点**：博客页面在服务端组件（SSR）中 fetch，Node 环境下相对路径
> （`/api/v1/...`）会直接抛 `Failed to parse URL from`，因此必须使用绝对地址。
> `public-posts.ts` 优先读 `API_SERVER_URL`，未设置时回退到 Docker 内部服务 DNS
> `http://nucpot-prod-api:8000`。任何让 `/blog` 渲染已发布文章的环境都必须保证
> 该地址可达，否则页面只会显示（可能为空的）种子文章，并在日志中输出 fetch 失败。

---

## 4. 部署

生产环境为自托管 Docker Compose（Mac Studio runner），不是 Vercel：

- Web 服务：`docker-compose.prod.yml` 中的 `web`（`docker/web.Dockerfile`，容器名 `nucpot-prod-web`），已注入 `API_SERVER_URL=http://nucpot-prod-api:8000`。
- API 服务：容器名 `nucpot-prod-api`（端口 8000）。
- 域名：`nucpot.dpdns.org` 由 nginx（`docker/nginx.prod.conf`）终结 TLS 并反代。

部署入口与操作规范见
[docs/runbooks/prod-deploy.md](./runbooks/prod-deploy.md)
（compose gate 生效后须通过 `run-deploy.sh` / GitHub `production-deployment` workflow 执行）。

发布文章**不需要重新部署**：管理后台发布 → 数据库 → ISR 在约 60 秒内刷新公开页面。

---

## 5. 本地开发

```bash
# 1. 启动 API（端口 8000）
pnpm dev:api

# 2. 启动 Web（端口 3000），显式指向本地 API
cd apps/web
API_SERVER_URL=http://localhost:8000 pnpm dev
```

- 访问 `http://localhost:3000/blog`。
- 若本地 API 未启动，`public-posts.ts` 会回退到 `apps/web/content/blog/` 的种子文章（控制台有 fetch 失败日志）。
- 新增种子文章：在 `apps/web/content/blog/` 下创建带 frontmatter（`title` / `date` / `summary` / `tags` / `author`）的 `.md` 文件。

---

## 6. 常见问题（FAQ）

### Q1: 管理后台发布后 `/blog` 看不到新文章？
1. 确认文章状态为 published（API `/api/v1/blog/public` 能否返回它）。
2. ISR 间隔 60 秒，稍等或强制刷新。
3. 检查 web 容器日志是否有 `[blog] public posts fetch failed` —— 通常是 `API_SERVER_URL` 不可达或用了相对路径。

### Q2: 构建期 `/blog` 页面超时失败？
`next build` 期间 `public-posts.ts` 直接使用文件系统种子文章，不发起 API 请求；若构建仍失败，检查种子目录与 frontmatter 格式（`title`、`date` 为必填）。

### Q3: 容器内 fetch 失败但宿主机 curl 正常？
SSR fetch 走的是容器网络。确认 `API_SERVER_URL` 指向的是容器可达地址（compose 网络内为 `http://nucpot-prod-api:8000`，而不是宿主机 localhost）。

### Q4: 如何编辑/删除已发布文章？
在 `/admin/blog` 管理后台操作；数据以数据库为准，无需改动仓库内 Markdown。

---

*最后更新：2026-09-18 | 维护者：NucPot 团队*
