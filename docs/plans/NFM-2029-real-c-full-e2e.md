# C-full 真实 1+N E2E 修复计划（NFM-2029）

## 目标

把当前“ASGI + 内存 SQLite + 私有 hook 模拟”的测试提升为可重复执行的真实系统验证：

- 1 个 Hub FastAPI + PostgreSQL；
- 4 个 Resource Node 运行进程；
- 节点通过真实 HTTP 完成注册、心跳、上传、断点续传、离线队列同步；
- 网络中断后操作不丢失，恢复后幂等重放；
- Hub 持久化同步水位、操作日志和冲突；
- Docker Compose 和 CI 均能一键运行；
- 原有快速 E2E 继续保留，用于快速反馈。

## 已确认基线

| 层 | 现状 | 证据 | 判定 |
|---|---|---|---|
| Hub 节点 CRUD | 注册、列表、详情、状态、心跳、统计、删除已实现 | `apps/api/src/nfm_db/api/v1/hub_nodes.py` | 部分完成 |
| Hub 路由注册 | `/api/v1/hub/nodes/*` 已注册 | `apps/api/src/nfm_db/main.py` | 完成 |
| 上传 API | `/api/v1/upload/init|chunk|complete|resume` 已实现 | `apps/api/src/nfm_db/api/v1/upload.py` | 部分完成 |
| 上传服务 | 分块落盘、hash 校验、resume 已实现 | `apps/api/src/nfm_db/services/chunk_upload_service.py` | 部分完成 |
| 持久化模型 | hub/resource/upload/data_dna/classification/ingest_log 已定义 | `apps/api/src/nfm_db/models/` | 模型存在 |
| 初始迁移 | revision 032 创建六张表 | `apps/api/migrations/versions/032_create_data_submission_tables.py` | 存在漂移风险 |
| Client 注册/心跳 | `NfmNodeClient` 已实现 HTTP 调用 | `apps/nfm-node-client/src/nfm_node_client/client.py` | 契约漂移 |
| Client 上传 | 调用不存在的 `/{node_id}/upload`，且只 init 不传 chunk | 同上 | 未闭环 |
| Client sync status | 调用不存在的 `/sync-status`，Hub 实际是 `/sync-stats` | 同上 | 未闭环 |
| SyncEngine | `_fetch_*` 返回空；`_push_local_changes` 只消费本地队列 | `sync_engine.py` | production no-op |
| SyncManager | `_send_operation` 是 no-op placeholder | `sync_manager.py` | production no-op |
| OfflineQueue | `dequeue()` 先删除，再发送 | `offline_queue.py` | 网络失败会丢操作 |
| Compose | 引用两个不存在的 Dockerfile | `docker-compose.e2e.yml` | 不可构建 |
| Resource runtime | 只有 SDK，无 daemon/entrypoint | `apps/nfm-node-client/` | 不可作为容器运行 |
| 快速 E2E | 36/36 通过 | `tests/e2e/` | 只证明模拟链路 |

## 关键架构决策

### D1：保留两层测试

1. `tests/e2e/`：快速 ASGI/SQLite/transport fake 测试；
2. `tests/e2e_live/`：真实 Docker/PostgreSQL/HTTP/进程重启/网络分区测试。

两者不能互相替代。

### D2：统一 Hub API 契约

权威路径：

```text
POST /api/v1/hub/nodes/register
POST /api/v1/hub/nodes/{node_id}/heartbeat
GET  /api/v1/hub/nodes/{node_id}/sync-stats

POST /api/v1/upload/init
POST /api/v1/upload/chunk
POST /api/v1/upload/resume
POST /api/v1/upload/complete

GET  /api/v1/hub/nodes/{node_id}/sync-data?since=<watermark>
POST /api/v1/hub/nodes/{node_id}/sync-data
```

Node Client 必须跟随现有上传 API，而不是继续维护第二套 `/{node_id}/upload` 契约。

### D3：Transport 注入，而不是覆写私有方法

新增 `HubTransport` protocol 和 `HttpHubTransport`：

```text
fetch_all_records
fetch_incremental_records
push_operation
upload_file
get_sync_status
close
```

`SyncEngine` 默认使用真实 transport；测试显式注入 fake/partition transport。

### D4：队列采用 claim → ack/nack

禁止发送前删除：

```text
pending → in_flight → completed(delete)
                  └→ pending(retryable failure)
                  └→ failed(permanent failure)
```

每个 operation 带稳定 `operation_id`，Hub 用唯一约束实现幂等。

### D5：数据库迁移是模型的唯一落地路径

不得靠 `Base.metadata.create_all()` 掩盖 PostgreSQL schema 漂移。真实 E2E 必须执行：

```text
alembic upgrade head
```

并校验单一 head、模型与 DB 列一致。

### D6：Compose 不占用 8000

Resource Node 访问 `http://hub:8000`（容器网络）；宿主机默认暴露：

```text
${E2E_HUB_PORT:-18000}:8000
```

## 实施 Wave 与依赖

```text
W0 契约审计
 ├─> W1 Hub sync API + persistence
 └─> W2 Client transport + queue claim/ack
          W1 + W2
             ↓
       W3 Resource daemon + E2E images
             ↓
       W4 真实 5 节点场景
             ↓
       W5 CI gate + 最终报告
```

### W0：契约与迁移审计

- [x] Hub 路由盘点
- [x] Node Client no-op 盘点
- [x] Compose/Dockerfile 盘点
- [ ] Alembic heads 与 revision 可达性验证
- [ ] ORM ↔ migration 字段差异验证
- [ ] 冻结 request/response schema

验收：形成完整差距矩阵，后续不再边写边猜接口。

### W1：Hub 同步 API 与持久化

- 新增同步 operation 模型、唯一 operation ID；
- 新增 per-node change log/watermark；
- 实现 push/fetch API；
- 更新 `ResourceNode.sync_watermark`；
- 写 ingest audit log；
- 冲突关联 resource node；
- migration + PostgreSQL 集成测试。

验收：真实 HTTP push 后可从另一个 client 增量 fetch；重复 operation 不重复落库。

### W2：Node Client 真实 transport

- 新增 transport protocol/HTTP 实现；
- 修正 ApiResponse envelope 解析；
- 对齐 heartbeat、upload、sync-stats 路径；
- 实现 init/chunk/resume/complete；
- `SyncEngine` 接入 transport；
- `SyncManager` 消除 no-op；
- OfflineQueue 改为 claim/ack/nack；
- client 重启后恢复 pending/in-flight 操作。

验收：不覆写 `_fetch_*` 私有方法即可连接真实 Hub。

### W3：E2E Runtime 与 Compose

- `docker/e2e-hub.Dockerfile`；
- `docker/e2e-resource.Dockerfile`；
- Resource daemon/entrypoint；
- Hub migration/seed；
- 4 个 node 独立 SQLite volume；
- 默认端口 18000；
- healthcheck 检查注册状态而非仅端口；
- Compose build-reference 静态检查。

验收：`docker compose up --build -d` 后 5 个服务健康、4 个节点自动注册。

### W4：真实网络 E2E

- 注册 + heartbeat；
- 真实文件分块上传；
- 中断后 resume；
- resource 进程重启后继续；
- 网络 disconnect 时 pending 不丢；
- reconnect 后幂等重放；
- watermark 单调递增；
- conflict 可查询/解决；
- DB 最终一致性。

验收：所有场景通过，且每条结论有 HTTP + DB + container 证据。

### W5：CI 与交付

- fast suite 与 live suite 分 job；
- JUnit + JSON + topology + DB integrity artifacts；
- Compose 必须 `down -v` 清理；
- PR 检查 Backend lint/tests、Node client tests、Docker build、live E2E；
- CI green 后才报告完成。

## 完成定义

只有全部满足才可称为“完整修复”：

1. 快速 E2E 全绿；
2. 真实 5 节点 Compose 可构建且健康；
3. 真实 HTTP 注册/心跳/上传/同步通过；
4. 节点或 Hub 重启后可恢复；
5. 网络故障不丢操作；
6. 重放无重复副作用；
7. PostgreSQL migration 从空库可升级到 head；
8. 报告场景数与实际执行数一致；
9. PR CI 全绿；
10. 不依赖手工 hot patch 或宿主机临时进程。
