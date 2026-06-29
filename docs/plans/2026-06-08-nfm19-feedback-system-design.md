# NFM-19: 势函数网站用户反馈体系设计

**日期**: 2026-06-08
**状态**: Draft
**负责人**: CPO
**父级 Issue**: NFM-17 (势函数网站运营维护)

## 1. 设计目标

为势函数（NucPot）网站建立用户反馈收集、分类和处理流程，确保科研用户的意见能高效传递给技术团队，形成运营→技术的闭环。

## 2. 方案选择

| 方案 | 描述 | 优势 | 劣势 |
|------|------|------|------|
| A: 纯邮箱 | 网站显示联系邮箱 | 最快上线 | 数据非结构化，无法追踪 |
| **B: 表单 + API** ⭐ | 网站反馈表单 → FastAPI 端点 → PostgreSQL | 结构化数据，可追踪，契合现有技术栈 | 需要开发工作量 |
| C: 第三方服务 | Tally / Typeform / GitHub Issues | 零代码 | 数据外流，中文支持差，无法集成 NFM 平台 |

**选定方案 B**：结构化反馈表单 + 后端 API，理由：
- 契合 FastAPI/Next.js 现有架构
- 结构化数据支撑分类、统计和报告需求
- 为未来 NFM 平台统一反馈系统奠定基础

## 3. 系统架构

### 3.1 前端：反馈表单组件

**位置**: 每个页面底部固定浮动按钮，点击展开反馈表单模态框。

**表单字段**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| feedback_type | 单选 | 是 | Bug 报告 / 功能建议 / 数据纠错 / 使用咨询 |
| title | 文本 | 是 | 问题简要描述（≤100 字） |
| description | 多行文本 | 是 | 详细描述（≤2000 字） |
| page_url | 自动获取 | 否 | 反馈时所在页面 URL |
| contact_email | 文本 | 否 | 用户联系方式（可选） |
| screenshot | 文件上传 | 否 | 截图附件（≤5MB，仅图片） |

**交互流程**:
1. 用户点击右下角浮动按钮（"意见反馈"）
2. 弹出模态框，选择问题类型
3. 填写描述信息
4. 提交后显示成功提示 + 感谢语
5. 同时在页面 footer 提供邮箱备选联系方式

### 3.2 后端：API 端点

**POST /api/v1/feedback**

请求体（JSON）:
```json
{
  "feedback_type": "bug_report",
  "title": "势函数计算页面无法加载",
  "description": "点击 CeO₂ 势函数查询后页面显示 500 错误",
  "page_url": "https://nucpot.example.com/potentials/ceo2",
  "contact_email": "user@example.com"
}
```

响应:
```json
{
  "success": true,
  "data": {
    "id": "fb_abc123",
    "feedback_type": "bug_report",
    "priority": "high",
    "status": "open",
    "created_at": "2026-06-08T10:30:00Z"
  }
}
```

**GET /api/v1/feedback** (管理员接口，需鉴权)

查询参数: `status`, `priority`, `feedback_type`, `page`, `limit`

### 3.3 数据模型

**feedbacks 表**:

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| feedback_type | ENUM | bug_report / feature_request / data_correction / usage_inquiry |
| title | VARCHAR(100) | 简要描述 |
| description | TEXT | 详细描述 |
| page_url | VARCHAR(500) | 反馈页面 URL |
| contact_email | VARCHAR(255) | 用户联系方式 |
| priority | ENUM | urgent / high / medium / low |
| status | ENUM | open / classified / assigned / in_progress / resolved / closed |
| assignee | VARCHAR(100) | 处理人 |
| resolution | TEXT | 处理结果 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |
| resolved_at | TIMESTAMP | 解决时间 |

**优先级自动判定规则**:
| feedback_type | 默认 priority |
|---------------|---------------|
| bug_report | medium（如 page_url 含关键词或描述含 "不可用"/"500"/"崩溃" → 自动升级为 high） |
| data_correction | high |
| feature_request | low |
| usage_inquiry | medium |

## 4. 反馈处理流程

```
用户提交反馈
    ↓
自动分类（feedback_type + priority 判定）
    ↓
运营人员初审（1 个工作日内）
    ├── 确认分类 → 分配处理人
    ├── 调整优先级 → 分配处理人
    └── 重复/无效 → 关闭并记录原因
    ↓
处理人处理（按 SLA 时效）
    ↓
处理完成 → 更新状态 + 记录处理结果
    ↓
（如用户提供邮箱）发送处理结果通知
    ↓
关闭反馈
```

## 5. SLA 定义

| 优先级 | 响应时间 | 处理时间 | 适用场景 |
|--------|----------|----------|----------|
| 紧急 (urgent) | 2 小时 | 8 小时 | 网站不可用、数据严重错误 |
| 高 (high) | 24 小时 | 3 个工作日 | 核心功能异常、数据纠错 |
| 中 (medium) | 48 小时 | 5 个工作日 | 体验问题、使用咨询 |
| 低 (low) | 72 小时 | 10 个工作日 | 功能建议、优化建议 |

## 6. 运营周报模板

每周一生成上周（周一至周日）反馈运营周报，包含：

1. **可用性概览**: 网站可用率、平均响应时间、故障次数
2. **反馈统计**: 新增反馈数、按类型分布、按优先级分布
3. **处理情况**: 已解决数、平均处理时长、SLA 达标率
4. **待处理清单**: 各状态下的反馈列表（open/classified/assigned/in_progress）
5. **本周重点**: 需要关注的问题和改进计划

## 7. 验收标准

- [ ] 用户反馈渠道上线并可用（浮动按钮 + 表单 + 邮箱备选）
- [ ] 反馈分类标准文档完成
- [ ] 反馈处理流程文档完成
- [ ] 运营报告模板完成
- [ ] 后端 API 端点可用
- [ ] 数据库表创建完成

## 8. 实施范围分解

### Phase 1: 文档 + 流程（本 Issue）
- 反馈分类标准文档
- 反馈处理流程文档
- 运营周报模板

### Phase 2: 后端实现（子 Issue）
- 数据库 migration（feedbacks 表）
- POST /api/v1/feedback 端点
- GET /api/v1/feedback 管理端点
- 反馈状态变更端点

### Phase 3: 前端实现（子 Issue）
- 反馈浮动按钮组件
- 反馈表单模态框
- 表单提交逻辑
- 页面 footer 邮箱联系方式
