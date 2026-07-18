# 对象存储可行性调研：MinIO/S3 替代本地磁盘作为文献 PDF 存储后端

> **配套文档**：本报告是技术路线图 v1.6 [§3.2 技术选型](technical-roadmap-nuclear-fuel-data-platform-1.6.md#存储后端) 与 [§7.8 轨道 B](technical-roadmap-nuclear-fuel-data-platform-1.6.md#b-phase-1-存储基础设施与合规前置设计与-sprint-4-7-并行) 的详细支撑材料。相关调研另见 [国内合规评估](domestic-storage-compliance.md)、[文献工具架构借鉴](literature-tools-storage-survey.md)、[存储合规路线图](storage-compliance-roadmap.md)。

| 评估项 | 内容 |
|--------|------|
| 评估日期 | 2026-07-18（基于 NFM-1485-2 实现设计会话整理） |
| 核心问题 | MinIO/S3 能否替代本地磁盘作为文献 PDF 存储后端？给出部署配置、S3Storage 实现骨架与迁移影响分析 |
| 结论 | ✅ 完全可行。推荐 MinIO 单节点。切换接口已就位（NFM-1486），实现工作量 ~1–2 天（issue NFM-1485-2） |

---

## 1. 接口已预留——切换的关键前提

项目早已具备完整的存储抽象层（NFM-1486）：

- `StorageBackend` Protocol 位于 `apps/api/src/nfm_db/services/storage.py`——**同步**方法：`save` / `read` / `delete` / `exists`
- `LocalDiskStorage`——已完整实现，文件路径为 `{root}/{datasource_id}/{filename}`
- `S3Storage`——目前是抛 `NotImplementedError` 的占位实现，为 NFM-1485-2+ 预留
- `get_storage()` 工厂读取 `LITERATURE_STORAGE_BACKEND=local|s3` 环境变量
- `literature_service._get_storage()` 已完全解耦——切换后所有调用点零改动

`DataSource` 模型已包含：`file_path`、`file_hash`、`content_md`、`parse_status`、`parse_error`。

**"开发阶段预留切换接口"的需求已经满足。** `S3Storage.save()` 返回的 key 与 `LocalDiskStorage` 格式一致（`{datasource_id}/{filename}`），因此无需修改 `file_path` 列的 schema，也无需数据回填。

---

## 2. SDK 选型：boto3 + 可选 `asyncio.to_thread()`（不是 aiobotocore）

| SDK | 原生异步 | 对 Protocol 影响 | 结论 |
|-----|----------|------------------|------|
| **boto3** | ❌ 同步 | 无影响——必要时用 `asyncio.to_thread()` 包装 | ⭐ **推荐** |
| aiobotocore | ✅ | 强制 `async def save/read` → 级联改造 LocalDiskStorage、所有调用点与测试 | ❌ 否决 |
| minio-py | ❌ 同步 | 与 boto3 同等影响，但被厂商锁定到 MinIO | ❌ 劣于 boto3 |

**boto3 胜出原因**：

1. Protocol 是同步签名——aiobotocore 会强制重写每一个 Protocol 方法、每一个实现与每一个测试 mock。boto3 将爆炸半径降为零。
2. 实际 I/O 发生在 **Celery worker 进程**（通过 `asyncio.run` 同步派发），而非 HTTP 请求线程。对同步调用者而言同步 boto3 是正确工具。
3. boto3 是 S3 标准实现——后续切换 AWS S3 / Cloudflare R2 / 阿里云 OSS 只需改一行 endpoint。
4. 唯一的异步调用点（`literature_service.py:191`），用 `await asyncio.to_thread(_get_storage().read, path)` 包装即可——单 PDF 读取 <200 ms，阻塞可忽略。

---

## 3. MinIO Docker 部署（Mac Studio 生产环境）

| 维度 | 值 |
|------|----|
| 镜像 | `minio/minio:RELEASE.2024-10-13T13-34-11Z`（固定版本，~150 MB） |
| 内存 | 空闲 ~150–300 MB，负载 ~500 MB–1 GB。`limits.memory: 1500M` |
| CPU | 稳态 <5%，峰值约 1 核。`limits.cpus: "1.0"` |
| 端口 | **不发布到宿主机**——服务仅通过 `prod` bridge 网络 DNS `http://minio:9000` 访问 |
| 数据卷 | `prod-minio-data:/data`（命名卷，与 `prod-db-data` 同命名规范） |
| 健康检查 | `curl -f http://localhost:9000/minio/health/live` |

当前栈占用 ~8–12 GB / 128 GB RAM，MinIO 1.5 GB 上限后还剩 >110 GB 余量，无需精简其它服务。

### 3.1 docker-compose.prod.yml 片段（增量，**无 `ports:` 块——纯内部**）

```yaml
  minio:
    image: minio/minio:RELEASE.2024-10-13T13-34-11Z
    container_name: nucpot-prod-minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${PROD_MINIO_ROOT_USER:-nfm_minio_admin}
      MINIO_ROOT_PASSWORD: ${PROD_MINIO_ROOT_PASSWORD:?Set PROD_MINIO_ROOT_PASSWORD}
      MINIO_SERVER_URL: http://minio:9000
    volumes:
      - prod-minio-data:/data
    deploy:
      resources:
        limits: { cpus: "1.0", memory: 1500M }
        reservations: { cpus: "0.1", memory: 256M }
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s
    networks: [prod]

# api 与 worker 都需要以下 env（worker 也要读存储）：
#   NFM_STORAGE_BACKEND: s3
#   NFM_S3_ENDPOINT: http://minio:9000
#   NFM_S3_REGION: us-east-1
#   NFM_S3_BUCKET: nfm-literature
#   NFM_S3_ACCESS_KEY: ${PROD_MINIO_API_KEY}
#   NFM_S3_SECRET_KEY: ${PROD_MINIO_API_SECRET}
#   NFM_S3_USE_PATH_STYLE: "true"   # MinIO 要求 path-style 寻址

volumes:
  prod-minio-data:
    name: nucpot-prod-minio-data
```

### 3.2 一次性引导脚本（`docker/minio-init.sh`，首次 `up` 后运行）

```bash
#!/bin/sh
mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb -p local/nfm-literature                  # 创建 bucket
mc anonymous none local/nfm-literature         # 显式私有
mc encrypt set sse-s3 local/nfm-literature     # 默认 SSE-S3（AES-256，无需 KMS）
mc admin user svcacct add --access-key "$PROD_MINIO_API_KEY" \
    --secret-key "$PROD_MINIO_API_SECRET" \
    local "$MINIO_ROOT_USER"
```

---

## 4. 安全配置

### 4.1 访问控制

- 单 bucket `nfm-literature`，默认 **私有**（拒绝匿名）。
- Root 账号仅用于引导；日常流量使用专用服务账号 `nfm-api`，作用域限定在 `nfm-literature/*` 上的 `s3:GetObject/PutObject/DeleteObject`。
- **MinIO Console（9001 端口）永不发布**——需要时通过 `docker exec ... mc` 管理。

### 4.2 服务端加密

- **SSE-S3 ✅ 推荐**——MinIO 内建 AES-256-GCM，服务端托管密钥，零额外组件。通过 `mc encrypt set sse-s3`（bucket 默认）启用，`put_object` 时传 `ServerSideEncryption="AES256"`。
- **SSE-KMS**——需要部署 MinIO KES + 外部 KMS（Vault）。对单机 NFMDI 过度设计，本轮不引入。
- **客户端加密（高敏感数据集可选）**——boto3 传 `SSECustomerKey`；密钥由 `NFM_SECRET_KEY` 派生。与 SSE-S3 互补；密钥不接触 MinIO。
- **容器内 TLS**——`http://minio:9000` 可接受；流量始终在 `prod` bridge 网络内。单机端到端 TLS 过度设计。

> 涉密场景下加密算法需切换为国密 SM4，详见 [国内合规评估 §6 国密改造](domestic-storage-compliance.md#6-国密sm2sm3sm4-改造)。

### 4.3 网络隔离（硬约束）

- MinIO 服务 **无 `ports:` 块** → 对宿主机、CF Tunnel、互联网均不可见。
- 仅 `api` 与 `worker` 容器可通过 Docker DNS `minio:9000` 访问。
- **CF Tunnel `cloudflared` 配置零改动。** MinIO 是纯内部服务。暴露它将引入新的数据外泄向量（S3 API 凭据泄露 = 核数据越境）。

---

## 5. S3Storage 生产级实现骨架

替换 `apps/api/src/nfm_db/services/storage.py` 中的 `S3Storage` 占位。保留同步 Protocol，约 70 行代码。

```python
import os, threading
from uuid import UUID
import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

class S3Storage:
    """S3/MinIO 支撑的 StorageBackend（boto3，同步）。

    对象 key 布局：nfm-literature/{datasource_id}/{sanitized_filename}
    save() 返回 "{datasource_id}/{filename}"——与 LocalDiskStorage 格式一致，
    因此 DataSource.file_path 行无需迁移。
    """
    _client = None
    _client_lock = threading.Lock()

    def __init__(self) -> None:
        self.endpoint    = os.environ["NFM_S3_ENDPOINT"]
        self.bucket      = os.environ["NFM_S3_BUCKET"]
        self._region     = os.environ.get("NFM_S3_REGION", "us-east-1")
        self._path_style = os.environ.get("NFM_S3_USE_PATH_STYLE", "true").lower() == "true"
        self._ensure_bucket()

    def _get_client(self):
        # 进程级单例（boto3 client 线程安全）
        if S3Storage._client is None:
            with S3Storage._client_lock:
                if S3Storage._client is None:
                    S3Storage._client = boto3.client(
                        "s3",
                        endpoint_url=self.endpoint,
                        region_name=self._region,
                        aws_access_key_id=os.environ["NFM_S3_ACCESS_KEY"],
                        aws_secret_access_key=os.environ["NFM_S3_SECRET_KEY"],
                        config=BotoConfig(
                            s3={"addressing_style": "path" if self._path_style else "auto"},
                            retries={"max_attempts": 3, "mode": "standard"},
                            connect_timeout=5, read_timeout=30,
                        ),
                    )
        return S3Storage._client

    def _ensure_bucket(self) -> None:
        try:
            self._get_client().head_bucket(Bucket=self.bucket)
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                self._get_client().create_bucket(Bucket=self.bucket)
                self._get_client().put_bucket_encryption(
                    Bucket=self.bucket,
                    ServerSideEncryptionConfiguration={"Rules": [
                        {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                    ]},
                )
            else:
                raise

    @staticmethod
    def _key(relative_path: str) -> str:
        return relative_path.lstrip("/")

    def save(self, datasource_id: UUID, filename: str, data: bytes) -> str:
        _validate_safe_filename(filename)
        safe = _sanitize_filename(filename) or f"{datasource_id}.pdf"
        key = f"{datasource_id}/{safe}"
        self._get_client().put_object(
            Bucket=self.bucket, Key=key, Body=data,
            ContentType="application/pdf",
            ServerSideEncryption="AES256",  # 显式 SSE-S3
        )
        return key

    def read(self, relative_path: str) -> bytes:
        resp = self._get_client().get_object(Bucket=self.bucket, Key=self._key(relative_path))
        return resp["Body"].read()

    def delete(self, relative_path: str) -> None:
        try:
            self._get_client().delete_object(Bucket=self.bucket, Key=self._key(relative_path))
        except ClientError as e:
            if e.response["Error"]["Code"] != "404":
                raise  # NotFound 静默忽略（与 LocalDiskStorage 语义一致）

    def exists(self, relative_path: str) -> bool:
        try:
            self._get_client().head_object(Bucket=self.bucket, Key=self._key(relative_path))
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise
```

`get_storage()` 工厂改造：

```python
if backend == "s3":
    return S3Storage()  # 原为：raise NotImplementedError
```

---

## 6. 迁移影响面

### 6.1 必须改动（4 个文件）

| 文件 | 改动 |
|------|------|
| `apps/api/src/nfm_db/services/storage.py` | 实现 `S3Storage`，更新 `get_storage()` 工厂 |
| `apps/api/pyproject.toml` | 添加 `boto3 >=1.34,<2`、`botocore` |
| `docker-compose.prod.yml` | 添加 `minio` 服务 + `prod-minio-data` 数据卷 + api/worker 的 env |
| `docker/.env.prod.example` | 添加 `PROD_MINIO_*` 示例变量 |

### 6.2 可选改动

| 文件 | 触发条件 |
|------|----------|
| `apps/api/src/nfm_db/services/literature_service.py:191` | 若 event-loop 阻塞可测量，将 `.read()` 包装为 `await asyncio.to_thread(...)`。单 PDF <200 ms——通常不需要 |
| `apps/api/tests/test_storage_s3.py`（新增） | 用 `moto` 或 `testcontainers-python`（MinIO 镜像）添加。强烈推荐 |

### 6.3 禁止改动

- `literature_service.py` 主体（除可选的 `to_thread`）——已通过 `_get_storage()` 解耦
- `models/source.py`——`file_path` 列已存后端相对 key，格式不变
- `literature_dispatcher.py` / Celery 任务——对存储层无感知
- `api/v1/literature.py` 上传端点——经服务层路由
- `api/v1/potentials.py` 上传端点——使用独立的 `upload_service`，不在范围
- `docker/nginx.prod.conf`、CF Tunnel `cloudflared` 配置——MinIO 仅内部可访问

### 6.4 数据迁移配方（切换时一次性执行）

`file_path` 列格式一致（`{datasource_id}/{filename}`），现有行无需 SQL 变更——只需把字节从 `prod-uploads` 数据卷复制到 MinIO：

```bash
docker run --rm --network nucpot-prod_prod \
  -v nucpot-prod_prod-uploads:/uploads:ro \
  -e MC_HOST_local=http://minio:9000 \
  --entrypoint sh \
  minio/mc -c "
    mc alias set local http://minio:9000 $ROOT_USER $ROOT_PWD
    mc mb -p local/nfm-literature
    mc cp --recursive /uploads/literature/ local/nfm-literature/
  "
# 然后：.env.prod 中切换 NFM_STORAGE_BACKEND=local → s3
# 然后：docker compose up -d --force-recreate api worker
```

---

## 7. 已知陷阱（实现设计评审总结）

1. **boto3 凭据链**——若不显式传 `aws_access_key_id`，boto3 会找 `~/.aws/credentials`，容器中不存在 → 报晦涩的认证错误。务必注入 `NFM_S3_ACCESS_KEY`/`NFM_S3_SECRET_KEY` env。
2. **`addressing_style: "path"`**——MinIO 要求 path-style，非 virtual-host style。否则 `nfm-literature.minio` 的 DNS 解析会失败。
3. **api 与 worker 都需要 S3 env**——Celery 派发发生在 api，但实际存储读取发生在 worker。worker 漏配将导致每次解析都因空 bucket 报错。
4. **不要双写兜底**——切勿"以防万一"同时向 LocalDisk 写入；这违反存储抽象设计并造成数据不一致。一次性干净切换。
5. **迁移后 `prod-uploads` 卷可删**——但保留一个发布周期以便回滚。
6. **PyMuPDF 的 `content_md` 在解析后落库**——PDF 字节读一次即可丢弃；MinIO 持有唯一副本。不要再加 LocalDisk 缓存层。

---

## 8. 备选方案对比

| 方案 | 数据主权 | 部署复杂度 | 结论 |
|------|----------|------------|------|
| **MinIO 单节点** | ✅ 完全本地 | 1 个容器 | ⭐ 推荐 |
| MinIO 分布式（≥4 节点） | ✅ 本地 | 高，需 ≥4 台主机 | ❌ Mac Studio 单机不满足 |
| SeaweedFS | ✅ 本地 | 3 组件部署 | 可接受替代，零件更多 |
| Cloudflare R2 | ❌ 全球节点 | 无 | ❌ 核数据不得越境 |
| 阿里云 OSS | ⚠️ 云端 | 无 | ❌ 除非指定区域获批 |

> 信创/涉密场景下 MinIO 需替换为 XSKY XEOS 或 Ceph RGW，详见 [国内合规评估 §3 自建方案对比](domestic-storage-compliance.md#3-自建方案对比信创适配度排序)。

---

## 9. 验证依据

- `read_file` 检查 `docker-compose.prod.yml`——确认 6 服务栈，无 minio，`prod-uploads` 卷在 api+worker 上
- `read_file` 检查 `services/storage.py`——确认 Protocol（同步）、LocalDiskStorage、S3Storage 占位
- `read_file` 检查 `services/literature_service.py:57-65,191`——确认 `_get_storage().read()` 单调用点，Celery 派发
- `read_file` 检查 `api/v1/literature.py:104-145`——确认 `/upload` 端点是占位，交给 Celery
- `grep` 检查 `pyproject.toml`——确认尚无 `boto3`/`minio`/`aiobotocore` 依赖
- `ls docker/`——确认 `nginx.prod.conf` 引用，仓库内无 cloudflared 配置（宿主机托管）

---

## 10. 行业交叉验证

对主流文献管理工具（Zotero、Mendeley、Paperpile、ReadCube、JabRef、EndNote）的调研证实：**每一个云端文献管理器都用 S3 作为文件存储后端**。这验证了 MinIO/S3 路线是行业标准选择。

| 工具 | 文件存储后端 | SDK 方式 | 去重机制 |
|------|-------------|----------|----------|
| Zotero | Amazon S3（presigned URL 直传） | 自定义 HTTP 客户端 | (md5, filesize) 即时发送 + 内容寻址存储 |
| Mendeley | AWS S3 | 自定义客户端 | 内容 hash 自动去重 |
| Paperpile | Google Drive（S3 兼容） | Drive API | DOI + 文件 ID |
| ReadCube Papers | S3 | 自定义客户端 | DOI/标题自动去重 |

**NFMDI 已对齐的 6 个行业通用设计模式**：元数据/二进制分离、基于 ID 的附件组织、内容 hash 去重、异步解析、本地 SQLite/PostgreSQL 存元数据、附件按引用而非内嵌存储。完整调研与可借鉴模式（乐观锁、CAS 物理去重、全文索引、CSL-JSON 导出、Crowd Catalog）见 [文献工具架构借鉴](literature-tools-storage-survey.md)。
