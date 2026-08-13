# 图数据库选型变更说明

> Neo4j → Apache AGE 替换方案
>
> 版本: v1.0 | 日期: 2026-07-30 | 密级: 项目内部

---

## 1. 背景与动机（Why）

### 1.1 部署简化

当前系统采用 PostgreSQL 作为主关系型数据库，知识图谱模块原计划使用 Neo4j 作为独立图数据库。这带来了以下运维负担：

- **独立进程管理**：Neo4j 需要独立部署、监控和运维，与 PostgreSQL 形成双数据库架构。
- **网络拓扑复杂度**：知识图谱服务与关系型数据服务之间需维护独立的连接池和心跳机制。
- **容器编排开销**：Kubernetes/Docker 环境下需额外管理 Neo4j 实例的扩缩容、数据卷持久化和备份策略。

### 1.2 信创兼容性

Apache AGE 以 PostgreSQL 扩展的形式运行，可复用现有 PostgreSQL 信创认证路径（如 openGauss、人大金仓等国产数据库的兼容模式）。而 Neo4j 作为独立商业产品，其信创适配路径尚不明确，在甲方信创合规评审中存在不确定性。

### 1.3 许可证成本

| 项目 | Neo4j Enterprise | Apache AGE |
|------|------------------|------------|
| 许可类型 | 商业许可（按节点计费） | Apache 2.0（开源免费） |
| 集群费用 | 高（3 节点起步） | 无（复用 PostgreSQL 实例） |
| 技术支持 | 付费订阅 | 社区支持 + 可购商业支持 |

采用 Apache AGE 可消除图数据库维度的许可证支出，同时将运维成本纳入已有的 PostgreSQL 技术栈。

---

## 2. 技术方案概述（What）

### 2.1 Apache AGE 简介

Apache AGE 是 Apache 基金会孵化项目，以 PostgreSQL 扩展的形式提供图数据库能力。其核心特性：

- **原生 PostgreSQL 扩展**：`CREATE EXTENSION age;` 即可启用，无需独立数据库进程。
- **Cypher 查询语言兼容**：AGE 支持 OpenCypher 语法，与 Neo4j 所采用的 Cypher 查询语言在语法层面具有 **90% 以上重叠率**。
- **SQL/图混合查询**：可在同一 SQL 查询中同时访问关系表和图数据，无需跨数据库 JOIN。
- **ACID 事务保障**：图操作与关系操作共享 PostgreSQL 事务，保证数据一致性。

### 2.2 Cypher 兼容性分析

AGE 实现了 OpenCypher 1.0 规范的核心子集，涵盖知识图谱模块所需的主要查询模式：

| 查询模式 | Neo4j Cypher | Apache AGE | 兼容性 |
|----------|-------------|------------|--------|
| 节点/边创建 | `CREATE (n:Label)` | `CREATE (n:Label)` | 完全兼容 |
| 属性匹配 | `MATCH (n) WHERE n.name = 'X'` | 相同语法 | 完全兼容 |
| 多跳遍历 | `MATCH p=(a)-[*1..3]->(b)` | 相同语法 | 完全兼容 |
| 聚合函数 | `COUNT`, `AVG`, `COLLECT` | 相同函数 | 完全兼容 |
| 路径最短 | `shortestPath()` | `shortestPath()` | 完全兼容 |
| 子图投影 | `CALL apoc.subgraph()` | 不适用 | 需用 SQL 替代 |
| 图算法库 | `gds` 库 | 内置基础算法 | 部分需自实现 |

> **结论**：知识图谱模块使用的核心 Cypher 查询模式（实体关系建模、多跳查询、属性匹配）均在 AGE 兼容范围内。少数高级特性（如 APOC 存储过程、GDS 算法库）可通过 SQL 扩展或应用层逻辑替代。

### 2.3 架构对比

```
方案 A：Neo4j（原方案）                方案 B：Apache AGE（推荐方案）
┌─────────────────────┐                ┌─────────────────────────────┐
│  Application Layer  │                │  Application Layer          │
├─────────────────────┤                ├─────────────────────────────┤
│  Graph Service      │                │  Graph Service              │
│  (Neo4j Driver)     │                │  (AGE Driver / psycopg2)   │
├─────────────────────┤                ├─────────────────────────────┤
│  Neo4j Database     │                │  PostgreSQL + AGE Extension │
│  (独立进程, 端口7687)│                │  (单一进程, 共享连接池)      │
├─────────────────────┤                ├─────────────────────────────┤
│  PostgreSQL         │                │  (已包含 PostgreSQL)        │
│  (关系型数据)       │                │                             │
└─────────────────────┘                └─────────────────────────────┘
  2 个数据库实例                           1 个数据库实例
  2 套连接池/备份/监控                    1 套连接池/备份/监控
```

---

## 3. 实施方案（How）

### 3.1 服务层抽象设计

为保障灵活性和可维护性，在知识图谱服务层引入**图数据库抽象接口**，支持 Neo4j 和 Apache AGE 之间的热切换：

```python
# 抽象接口
class GraphRepository(Protocol):
    """图数据库操作抽象接口"""

    async def create_node(self, label: str, properties: dict) -> str: ...
    async def create_edge(self, src_id: str, rel_type: str,
                          dst_id: str, properties: dict) -> str: ...
    async def query(self, cypher: str,
                    params: dict | None = None) -> list[dict]: ...
    async def delete_node(self, node_id: str) -> bool: ...

# Neo4j 实现（Plan B 后备）
class Neo4jRepository(GraphRepository):
    """基于 Neo4j Bolt 协议的实现"""
    ...

# Apache AGE 实现（当前推荐）
class AgeRepository(GraphRepository):
    """基于 PostgreSQL + AGE 扩展的实现"""
    ...
```

**关键设计原则**：

- 所有上层业务代码仅依赖 `GraphRepository` 接口，不直接调用特定图数据库驱动。
- 通过配置文件切换后端实现，无需修改业务逻辑代码。
- 查询语言统一使用 Cypher 语法（AGE 兼容子集），最大限度减少查询层迁移成本。

### 3.2 迁移步骤

| 阶段 | 工作内容 | 预估工期 |
|------|---------|---------|
| **阶段 1** | AGE 扩展安装、`age_repo` 实现开发、接口抽象层搭建 | 1 周 |
| **阶段 2** | 知识图谱核心查询迁移验证（实体创建、关系遍历、属性搜索） | 1 周 |
| **阶段 3** | 集成测试、性能基准测试、与现有 PostgreSQL 数据的混合查询验证 | 1 周 |

### 3.3 数据迁移

知识图谱数据存储于 AGE 的图结构中（使用 `create_graph()` 创建命名图），与关系型数据共存于同一 PostgreSQL 实例。新数据直接写入 AGE 图结构；若需从 Neo4j 迁移历史数据，可通过 Cypher 导出/导入脚本完成。

---

## 4. 风险缓解与应急预案（Risk）

### 4.1 已识别风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Cypher 语法不完全兼容导致查询失败 | 中 | 严格限定使用 OpenCypher 核心子集；编写查询兼容性测试用例 |
| AGE 性能不及 Neo4j（超大规模图） | 低 | 当前知识图谱规模在万级节点以内，AGE 性能够用；预留性能基准测试环节 |
| 社区活跃度与长期维护风险 | 低 | AGE 为 Apache 孵化项目，有 Microsoft、Bitnine 等企业持续贡献；Plan B 保障兜底 |

### 4.2 Plan B：Neo4j 回退方案

若甲方在评审后要求恢复 Neo4j 方案，我方承诺：

1. **回退窗口**：自文档提交甲方评审之日起 **2 周**内完成回退。
2. **回退操作**：
   - 切换配置项，将 `GraphRepository` 实现从 `AgeRepository` 替换为 `Neo4jRepository`。
   - 上层业务代码、API 接口、数据模型均无需修改。
   - 部署 Neo4j 容器，恢复独立图数据库连接。
3. **回退保障**：
   - 抽象接口设计确保回退为**配置级变更**，不涉及代码重构。
   - 回退期间原有 `AgeRepository` 实现保留于代码库中，不删除。
   - 回退完成后的 1 周内提供功能验证报告。

### 4.3 决策时间线

```
提交甲方评审 → 甲方反馈（1周）→ 决策：继续 AGE 或 回退 Neo4j
                                        │
                            ┌───────────┴───────────┐
                            │                       │
                      继续 AGE                  回退 Neo4j
                      阶段 1-3 实施            2 周内完成回退
```

---

## 附录

### A. 参考文档

- Apache AGE 官方文档：https://age.apache.org/
- OpenCypher 规范：https://opencypher.org/
- 合同 §1.2 知识图谱模块
- 技术路线图 v1.7 §12 R13

### B. 术语说明

| 术语 | 说明 |
|------|------|
| Apache AGE | Apache Graph Extension，基于 PostgreSQL 的图数据库扩展 |
| Cypher | 图数据库声明式查询语言，Neo4j 原创，OpenCypher 为其开放规范 |
| 信创 | 信息技术应用创新，指国产化替代的技术合规要求 |
| Plan B | 本文档中指回退至原 Neo4j 方案的应急预案 |
