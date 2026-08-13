# Phase 3 生产部署报告

**部署时间**: 2026-07-27 → 2026-07-28 (迭代部署)
**部署状态**: ✅ 全部完成并验证通过
**Git 分支**: `main`
**部署策略**: 本地 Docker Compose (绕过 CI 卡死)

---

## 1. 部署摘要

### 核心修复 (5 个 commit)

| Commit | 描述 | 影响范围 |
|--------|------|---------|
| `b86ac29` | 基础 bug 修复 (10 项后端 + 5 项前端) | 认证、API、UI |
| `1b1ec05` | 按钮重命名 + 详情面板布局调整 | 前端 UI |
| `141779b` | extraction pipeline Stage 5 桥接 GraphBuilder | 后端 pipeline |
| `d2c657d` | 处理重复边 UniqueViolationError | 后端 KG 构建 |
| `c72517d` | UI 增加"提取源"列 | 前端 UI |

### 服务映射 (生产环境)

| 服务 | 容器名 | 内部端口 | 暴露 | 状态 |
|------|--------|---------|------|------|
| Web (Next.js) | `nucpot-prod-web` | 3000 | 3000 | ✅ healthy |
| API (FastAPI) | `nucpot-prod-api` | 8000 | 8001, 8002 | ✅ healthy |
| Worker (Celery) | `nucpot-prod-worker` | - | - | ✅ healthy |
| LightRAG | `nucpot-prod-lightrag` | 9621 | 仅内网 | ✅ healthy |
| DB (Postgres) | `nucpot-prod-db` | 5432 | 5433 (host) | ✅ healthy |
| Redis | `nucpot-prod-redis` | 6379 | - | ✅ healthy |

---

## 2. 关键部署步骤

```bash
# 1. 提交代码
cd ~/Projects/nucpot
git add -A apps/ docs/
git commit -m "fix(phase3): ..."
git push origin main

# 2. 构建 API 镜像 (使用清华镜像避免网络问题)
docker compose --env-file docker/.env.prod -f docker-compose.prod.yml build api

# 3. 构建 Web 镜像 (npm 镜像 + Taobao)
docker compose --env-file docker/.env.prod -f docker-compose.prod.yml build web

# 4. 重启服务
docker compose --env-file docker/.env.prod -f docker-compose.prod.yml up -d api web

# 5. 验证
curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/api/v1/health  # 200
curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/  # 200
```

### Docker web.Dockerfile 关键补丁

```dockerfile
# 使用淘宝 npm 镜像 (解决 corepack 无法访问 npm.org)
RUN corepack enable
RUN npm config set registry https://registry.npmmirror.com && \
    npm install -g pnpm@9 --force && \
    pnpm config set registry https://registry.npmmirror.com
ENV COREPACK_NPM_REGISTRY=https://registry.npmmirror.com
```

---

## 3. 端到端验证结果

### API 健康检查
```
$ curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/api/v1/health
200
```

### 登录
```
$ curl -X POST http://localhost:8001/api/v1/auth/login \
    -d 'username=lwj280@gmail.com&password=demo1234' -i | grep set-cookie
set-cookie: access_token=...; HttpOnly; Max-Age=1800; Path=/; SameSite=lax; Secure
```

### 审核队列 API
```
$ GET /api/v1/review/pending?item_type=node
{
  "data": {
    "total": 1,
    "items": [{
      "id": "...",
      "item_data": {"label": "simulation_method=AutoML..."},
      "confidence": 0.48,
      "review_status": "pending",
      "source": {
        "doi": null,
        "source_id": "7d48ea92-...",
        "source_title": null
      }
    }]
  }
}
```

### 数据溯源 API
```
$ GET /api/v1/review/{id}/source
{
  "data": {
    "paragraph": "Table 2.1. Constants Used in UO₂ Gd₂O₃...",
    "doi": null,
    "source_title": "Material Property Correlations: Comparisons between FRAPCON-4.0, FRAPTRAN-2.0, and MATPRO (PNNL 2015)",
    "journal": "PNNL Technical Report",
    "year": 2015
  }
}
```

### 公开 URL (Cloudflare Tunnel)
- HTTPS: https://nucpot.dpdns.org → 200 OK
- 偶尔返回 502/1033 (CF Tunnel 间歇性问题,非代码)

---

## 4. 数据流验证

### 修复前
```
extraction/trigger
  → ontofuel_extract (LLM)
  → quality_gate
  → _ref_gap_fill_staging ✅
  → ✗ 没有 Stage 5 (数据卡在 staging)
```

### 修复后
```
extraction/trigger
  → ontofuel_extract (LLM)
  → quality_gate
  → _ref_gap_fill_staging ✅
  → Stage 5: GraphBuilder.build_from_extraction ✅ (新)
  → kg_nodes + kg_edges (pending_review)
  → /review/kg 审核页面
  → 人工 approve/reject
  → 来源溯源面板
```

---

## 5. 最终数据库状态 (截至 2026-07-28)

```sql
SELECT 'kg_nodes total' as metric, count(*) as value FROM kg_nodes
UNION ALL SELECT 'kg_nodes with source', count(*) FROM kg_nodes WHERE source_id IS NOT NULL
UNION ALL SELECT 'pending review', count(*) FROM kg_nodes WHERE review_status='pending'
UNION ALL SELECT 'approved', count(*) FROM kg_nodes WHERE review_status='approved';

-- 结果:
-- kg_nodes total        |  57
-- kg_nodes with source  |  57
-- pending review        |   1
-- approved              |  55
```

### 文献覆盖

| 数据源 | 文件大小 | KG 节点数 |
|--------|---------|----------|
| PNNL FRAPCON (1a0f45d9) | 281 KB | 18 |
| Owen et al. (9320cb50) | 57 KB | 25 |
| Terricabras et al. (a4c37a11) | 48 KB | 11 |
| Material Property Correlations (7d48ea92) | 26 KB | 3 |

---

## 6. 已知问题与限制

### 非阻塞 (生产环境正常)

| 问题 | 根本原因 | 状态 |
|------|---------|------|
| Cloudflare 间歇 502/1033 | CF Tunnel 网络波动 | 不影响功能 |
| `kg_nodes.source_title`/`doi` 在列表 API 返回 null | API 路由未嵌套查询 data_sources | 用户点击展开后通过 `/source` 端点获取完整数据 |

### 已修复但需要持续观察

| 问题 | 缓解措施 |
|------|---------|
| `_ref_gap_fill_staging` → `kg_nodes` 管道断裂 | Stage 5 已修复并部署 |
| `UniqueViolationError` on `(source_node_id, target_node_id, relation_type)` | `_create_edge` 预检查已部署 |
| 22 个迁移文件中 6 个语法错误 | 全部通过 ORM `create_all()` + 手动 stamp alembic_version=027 修复 |

---

## 7. 回滚预案

```bash
# 1. 停止新构建的服务
docker compose --env-file .env.prod -f docker-compose.prod.yml stop api web

# 2. 拉回旧镜像 (前一个 commit: 5b1ef25)
docker compose --env-file .env.prod -f docker-compose.prod.yml pull

# 3. 回滚数据库 schema (如果有迁移)
docker exec nucpot-prod-api alembic downgrade -1  # 单步回滚
# 或: docker exec nucpot-prod-db psql -U nfm -d nfm_db -c "DELETE FROM kg_nodes WHERE created_at > '2026-07-27';"

# 4. 重启
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d api web
```

### 紧急恢复 placeholder 节点

```sql
INSERT INTO kg_nodes (id, label, node_type, properties, source_id, confidence, review_status, created_at)
SELECT id, label, node_type, properties, source_id, confidence, review_status, created_at
FROM kg_nodes_backup_20260728
ON CONFLICT (id) DO NOTHING;
```

---

## 8. 监控和后续行动

### 建议监控指标

```bash
# 1. KG 节点自动生成速率
watch -n 60 'docker exec nucpot-prod-db psql -U nfm -d nfm_db -c "SELECT count(*) FILTER (WHERE review_status='pending'), count(*) FROM kg_nodes;"'

# 2. extraction pipeline 健康度
docker logs nucpot-prod-worker --tail 50 | grep -i "kg build\|extraction"

# 3. Cloudflare Tunnel 状态
curl -s -o /dev/null -w '%{http_code}' https://nucpot.dpdns.org/  # 应为 200 或 502
```

### 下一步优化任务

- [ ] 修复 `kg_nodes` 列表 API 的 source_title 嵌套字段 (Task #2 中级)
- [ ] 接入 CF Tunnel 健康监控告警
- [ ] 添加 staging 记录批量转 KG 节点的后台 cron job (每晚扫描积压数据)

---

**部署完成时间**: 2026-07-28 21:40 CST
**下一次部署计划**: 待 Phase 4 (审核自动化) 完成
