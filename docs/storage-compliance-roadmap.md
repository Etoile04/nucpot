# 存储与合规路线图：双轨集成方案

> **配套文档**：本报告是技术路线图 v1.6 [§7.8 轨道 B: 基础设施与合规](technical-roadmap-nuclear-fuel-data-platform-1.6.md#7-8-轨道-b-基础设施与合规) 的展开版，桥接正式路线图与下列三份调研报告：[对象存储可行性](object-storage-feasibility.md)、[国内合规评估](domestic-storage-compliance.md)、[文献工具架构借鉴](literature-tools-storage-survey.md)。

| 评估项 | 内容 |
|--------|------|
| 评估日期 | 2026-07-18 |
| 核心问题 | 如何把多份存储/合规技术调研编织进单一、决策门驱动的项目路线图？ |
| 方法论 | 双轨集成（Track A 功能开发 + Track B 基础设施与合规并行），用决策门把开放问题显式化为分叉点 |

---

## 1. 双轨集成方法论

当一系列相关技术评估产生多份报告后，"这些发现如何整合进路线图"是一个独立的交付物——它要求把多条调研线编织成一个**决策门驱动**的计划。

### 1.1 核心原则：技术评估与功能开发正交

技术评估（存储后端、合规、领域调研）与功能开发（Sprint 4–8）**正交**——并行进行。把它们映射为现有时间线上的两条轨道：

```
轨道 A: 功能开发（既有 Sprint）
    Sprint 4 → Sprint 5 → Sprint 6 → Sprint 7 → Sprint 8

轨道 B: 基础设施与合规（新增，源自调研）
    Phase 1（存储+前置设计）→ [决策门 A] → Phase 3（信创）→ Phase 4B（涉密）
```

### 1.2 关键陷阱：不要让合规阻塞功能开发

功能开发（Track A）**无论合规决策如何都照常推进**。调研得出的"零成本前置项"是 Track A 期间唯一必须做的事——其它一切都等决策门。这是防止合规轨道瘫痪项目的关键洞察。

---

## 2. 路线图集成的四大要素

| 要素 | 说明 | NFMDI 落地 |
|------|------|-----------|
| **决策门** | 调研中的开放问题被显式化为门。门前：两条路径都可行；门后：commit 一条 | "决策门 A：涉密与信创等级确认" 分叉为答案 A（不涉密，优化）vs 答案 B（涉密，全栈迁移） |
| **零成本前置设计** | 调研中"现在就做、成本为零但能省下昂贵返工"的项 | Hash 算法参数化（现在 SHA-256，未来 SM3——输出长度相同） |
| **交叉引用** | 每个路线图任务回链到具体调研章节，让路线图可审计 | 本文档与 v1.6 §7.8 每个任务均带调研文档锚点 |
| **三条时间路径** | 基于决策门答案的三条路径 | 答案 A（不涉密）：短路径，优化；答案 B（涉密）：长路径，9–15 个月含认证；答案 C（待定）：继续 Track A，并行启动评估 |

---

## 3. NFMDI 双轨全景

```
轨道 A: 功能开发
2026 Q3 (7-9月)                     2026 Q4 (10-12月)                    2027 Q1 (1-3月)
├───────────────────────────┤        ├───────────────────────────┤        ├───────────────────────────┤
│  Sprint 4: 智能设计核心   │        │  Sprint 6: 实验反馈闭环    │        │  Sprint 8: Agent产品化    │
│  Sprint 5: 工作台集成     │        │  Sprint 7: 数据治理增强    │        │                          │
└───────────────────────────┘        └───────────────────────────┘        └───────────────────────────┘
     核心能力建设（双轨共享）              闭环+治理+Agent基础                 Agent生态建设

轨道 B: 基础设施与合规（并行）
┌──────────────┐
│ B-Phase 1    │  存储+合规前置设计（与 Sprint 4-7 并行）
│ MinIO+FTS    │  hash参数化/LLM本地化/网络预留
└──────┬───────┘
       │
╔══════╧═══════╗
║ 决策门 A: 涉密 ║  ← 甲方/合规确认密级+信创要求
╚══════╤═══════╝
   ┌───┴────┐
不涉密│        │涉密/信创
   ▼        ▼
Phase 4A   B-Phase 3: 信创适配（2-3 月）
(持续优化)  鲲鹏+麒麟+XSKY+KingbaseES+国密
           B-Phase 4B: 涉密测评（6-12 月）
           等保三级+保密测评+商密认证
```

---

## 4. B-Phase 1: 存储基础设施与合规前置设计（与 Sprint 4–7 并行）

**目标**：跑通文献上传→解析→存储全链路，同时为零成本合规前置设计做准备。

| 任务 | 交付物 | 依赖 | 调研依据 |
|------|--------|------|----------|
| 文献上传管线 | POST /literature/upload 接收 PDF → storage.save() → Celery parse | Sprint 4 | [对象存储报告 §8](object-storage-feasibility.md#7-已知陷阱实现设计评审总结) |
| S3Storage 实现 | boto3 + MinIO docker-compose + 单例 client + SSE-S3 | 上一步 | [对象存储报告 §5](object-storage-feasibility.md#5-s3storage-生产级实现骨架) |
| 全文索引 | PG FTS tsvector + GIN 索引（content_md 已有） | 上一步 | [文献工具调研 §4.3](literature-tools-storage-survey.md#43-全文索引fts)（Zotero FTS5 借鉴） |
| 乐观锁 | DataSource version + If-Match header | — | [文献工具调研 §4.2](literature-tools-storage-survey.md#42-乐观锁optimistic-locking)（Zotero libraryVersion 借鉴） |
| **Hash 算法参数化** | sha256/SM3 环境变量可切换 | — | [合规评估 §7.2](domestic-storage-compliance.md#72-hash-参数化是零成本前置项) |
| **LLM 调用本地化验证** | 确认 Ollama 可独立运行，不依赖外部 API | — | [合规评估 §7.3](domestic-storage-compliance.md#73-架构抽象存活清单) |
| **网络入口预留** | nginx 反代配置模板，涉密时删 CF Tunnel | — | [合规评估 §7.3](domestic-storage-compliance.md#73-架构抽象存活清单) |

> **设计原则**：协议 ≠ 实现。S3 API、HTTP、SQL 协议本身无合规问题（国内所有主流云厂商全部兼容 S3 API），合规约束在数据存储的具体实现（存储位置、加密、审计）。`StorageBackend` 抽象层 + boto3 的设计保证切换只需改环境变量。

---

## 5. 决策门 A：涉密与信创等级确认

**这是最关键的决策点**，需甲方/合规负责人明确后才能启动 B-Phase 3。

| 确认项 | 答案 A（不涉密） | 答案 B（涉密/信创） | 答案 C（待定） |
|--------|------------------|---------------------|----------------|
| 数据密级？ | 公开/内部 | 秘密/机密 | 待评估 |
| 强制信创目录？ | 否 | 是 | 待确认 |
| 等保等级？ | 二级 | 三级/四级 | 待定 |
| 国密合规范围？ | 不要求 | 传输+存储+签名 | 待定 |
| 生产部署位置？ | Mac Studio/云 | 涉密机房 | 待定 |

**路径选择**：

- **答案 A** → B-Phase 4A（优化期，MinIO 继续用）
- **答案 B** → B-Phase 3 + B-Phase 4B
- **答案 C** → 按 B-Phase 1 继续，并行启动涉密定级咨询

---

## 6. B-Phase 3: 信创适配（如需，2–3 月）

**环境**：国产服务器（鲲鹏 920）+ 国产 OS（银河麒麟 V10 SP3）+ Docker CE

| 任务 | 周期 | 调研依据 |
|------|------|----------|
| 硬件采购：鲲鹏 920 服务器（2U/256GB/NVMe） | 1–2 月 | [合规评估 §6](domestic-storage-compliance.md#6-涉密信创全栈评估升级模式) |
| OS 部署：银河麒麟 V10 SP3 + Docker CE + 内网 Harbor/Nexus | 1–2 周 | [合规评估 §6](domestic-storage-compliance.md#6-涉密信创全栈评估升级模式) |
| 存储：MinIO → XSKY XEOS（改 endpoint_url，**零代码改动**） | 2–3 天 | [对象存储报告 §5](object-storage-feasibility.md#5-s3storage-生产级实现骨架) |
| 数据库：PG → 人大金仓 KingbaseES（PG 国产分支，改连接串） | 1–2 周 | [合规评估 §6](domestic-storage-compliance.md#6-涉密信创全栈评估升级模式) |
| 国密改造：SM3 替代 SHA-256 + SM4 替代 AES + SM2 替代 RSA + 国密 TLS | 2–4 周 | [合规评估 §7](domestic-storage-compliance.md#7-国密sm2sm3sm4-改造) |
| CI/CD 内网化：GitHub Actions → Gitea Actions | 1–2 周 | [合规评估 §6](domestic-storage-compliance.md#6-涉密信创全栈评估升级模式) |
| PDF 解析：PyMuPDF(AGPL) → pypdf/MinerU（如 AGPL 不被接受） | 2–3 天 | [合规评估 §6](domestic-storage-compliance.md#6-涉密信创全栈评估升级模式) |

> **架构资产保留度**：得益于 `StorageBackend` 抽象层和 SQLAlchemy ORM，**~80% 代码可原样迁移**。两个核心抽象——存储 Protocol 和 ORM——在迁移中几乎零损伤。真正的成本不在代码改造（1–2 月），而在合规测评认证（6–12 月）。

---

## 7. B-Phase 4B: 涉密部署（如需，6–12 月）

**环境**：涉密机房 + 物理隔离

| 任务 | 周期 | 调研依据 |
|------|------|----------|
| 物理隔离：删除 CF Tunnel + 全内网 nginx + LLM 本地化（Ollama + 国产模型 + 昇腾 GPU） | 4–8 周 | [合规评估 §8](domestic-storage-compliance.md#8-涉密三条红线bmb17-2006不可妥协) |
| 密级标识 + 介质管控系统（BMB17-2006 要求） | 2–4 周 | [合规评估 §8](domestic-storage-compliance.md#8-涉密三条红线bmb17-2006不可妥协) |
| 审计系统（独立审计日志，不可篡改） | 2 周 | [合规评估 §8](domestic-storage-compliance.md#8-涉密三条红线bmb17-2006不可妥协) |
| **等保三级测评**（第三方机构） | 3–6 月 | [合规评估 §9](domestic-storage-compliance.md#9-成本现实代码便宜认证昂贵) |
| **涉密保密测评**（国家保密局指定机构） | 3–6 月 | [合规评估 §9](domestic-storage-compliance.md#9-成本现实代码便宜认证昂贵) |
| **商密产品认证**（如需 GM/T 0028） | 2–4 月 | [合规评估 §9](domestic-storage-compliance.md#9-成本现实代码便宜认证昂贵) |

> **三条红线（不可妥协）**：① 物理隔离（CF Tunnel 必须删，全内网）；② 国密算法（SHA-256→SM3、AES→SM4、RSA→SM2、TLS→国密 SSL，全部改造）；③ 介质管控+密级标识（bucket 打密级标签，读写全审计，介质退役前物理销毁）。

---

## 8. B-Phase 4A: 非涉密优化期（如不涉密）

| 任务 | 调研依据 |
|------|----------|
| MinIO 监控+生命周期策略 | [对象存储报告 §4](object-storage-feasibility.md#4-安全配置) |
| asyncio.to_thread 异步优化 | [对象存储报告 §2](object-storage-feasibility.md#2-sdk-选型boto3--可选-asyncioto_thread不是-aiobotocore) |
| CAS 物理去重（语义层 `{ds_id}/` + 物理层 `_cas/{hash}/`） | [文献工具调研 §4.1](literature-tools-storage-survey.md#41-cascontent-addressed-storage物理去重)（Zotero CAS 借鉴） |
| 标准格式导出：CSL-JSON/BibTeX/RIS | [文献工具调研 §4.4](literature-tools-storage-survey.md#44-标准格式导出避免数据锁定)（避免数据锁定） |
| Crowd Catalog（远期，多用户元数据去重） | [文献工具调研 §4.5](literature-tools-storage-survey.md#45-crowd-catalog远期)（Mendeley 借鉴） |

---

## 9. 文档关系图

```
                    ┌─────────────────────────────────────┐
                    │  技术路线图 v1.6                     │
                    │  §3.2 技术选型                       │
                    │  §4.3 安全增强                       │
                    │  §7.8 轨道 B（本路线图的摘要版）     │
                    └────────────┬────────────────────────┘
                                 │ 展开
                                 ▼
                    ┌─────────────────────────────────────┐
                    │  存储合规路线图（本文档）            │
                    │  双轨集成 + 决策门 + Phase 1-4       │
                    └──┬──────────┬──────────┬─────────────┘
                       │          │          │
            ┌──────────▼──┐  ┌────▼─────┐  ┌─▼────────────────┐
            │ 对象存储    │  │ 国内合规 │  │ 文献工具借鉴     │
            │ 可行性      │  │ 评估     │  │                  │
            │ § MinIO     │  │ § S3辨析 │  │ § Zotero/Mendeley│
            │ § S3Storage │  │ § 信创   │  │ § CAS去重        │
            │ § docker    │  │ § 涉密   │  │ § 乐观锁         │
            │ § 迁移      │  │ § 国密   │  │ § FTS            │
            └─────────────┘  └──────────┘  └──────────────────┘
```

四份文档互为支撑：

- 想了解**怎么部署 MinIO** → [对象存储可行性](object-storage-feasibility.md)
- 想了解**"S3 合规吗"** → [国内合规评估](domestic-storage-compliance.md)
- 想了解**行业工具怎么设计存储** → [文献工具架构借鉴](literature-tools-storage-survey.md)
- 想了解**整体计划与决策点** → 本文档
