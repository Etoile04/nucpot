---
title: NucPot材料物性数据库架构设计
date: 2026-03-10
summary: 介绍NucPot核材料物性数据库的系统架构设计，包括技术选型、数据模型、API设计和可扩展性策略。
tags:
  - 数据库
  - 架构
  - NucPot
  - api
  - technical
author: 李思涵
---

# NucPot材料物性数据库架构设计

NucPot（Nuclear Fuel & Materials Properties Database）是中国首个可持续共享的核燃料与材料物性数据库平台。本文介绍其系统架构设计的技术决策和实现方案。

## 技术选型

### 后端技术栈

- **数据库**：PostgreSQL 16 — 支持 JSONB 数据类型、全文搜索、丰富的数据类型
- **Web框架**：FastAPI — 高性能异步 Python 框架
- **缓存层**：Redis — 热点数据缓存，降低数据库压力

### 前端技术栈

- **框架**：Vue 3 + Nuxt 3 — 服务端渲染，SEO 友好
- **UI 组件**：Ant Design Vue 5 — 企业级组件库
- **图表可视化**：ECharts — 支持大规模数据可视化

## 数据模型设计

数据库采用 16 张核心表，按功能模块划分为四个区域：

1. **基础数据模块**（5 张表）：元素、同位素、材料、化合物、相图
2. **物性数据模块**（6 张表）：物性数据主表、温度压力数据点、数据来源、实验方法、不确定度、参考文献
3. **文献来源模块**（3 张表）：期刊、作者、机构
4. **质量控制模块**（2 张表）：审核记录、数据版本

### 关键设计决策

物性数据采用 **温度-压力二维索引** 模型，每条物性记录关联多个温度和压力条件下的数据点。这使得用户可以精确查询特定工况下的材料性能。

## API 设计

采用 RESTful 风格，核心端点包括：

```
GET  /api/v1/materials                    # 材料列表
GET  /api/v1/materials/{id}/properties    # 材料物性查询
GET  /api/v1/properties/search             # 高级搜索
     ?temperature_min=300
     &temperature_max=1200
     &property_type=thermal_conductivity
```

## 可扩展性策略

### 缓存策略

- **一级缓存**：应用内存缓存（LRU，容量 1000 条）
- **二级缓存**：Redis 分布式缓存（TTL 1 小时）
- **缓存预热**：系统启动时加载热门材料数据

### 异步数据导入

使用消息队列（如 RocketMQ）实现异步数据导入管道，支持大批量文献数据的批量处理，避免阻塞主 API 响应。

### 搜索优化

PostgreSQL 全文搜索 + 中文分词（zhparser），支持材料名称、摘要、关键词的模糊搜索。
