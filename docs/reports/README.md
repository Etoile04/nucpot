# NucPot 巡检流程 (Inspection Workflow)

**版本**: 1.0
**生效日期**: 2026-06-11
**责任**: CPO / Release Engineer

---

## 概述

本文档定义 NucPot 平台的定期巡检流程，确保服务稳定性、安全性和运营质量。巡检分为三个层级：每日、每周和每月，各有明确的检查项、责任人和时间预算。

## 工具

| 工具 | 用途 | 位置 |
|------|------|------|
| `daily_check.py` | 半自动化每日巡检脚本 | `scripts/daily_check.py` |
| `health_check.py` | 站点健康检查（被 daily_check 调用） | `scripts/health_check.py` |

### 运行每日巡检

```bash
# 基本运行（跳过 GitHub Actions 检查）
python scripts/daily_check.py

# 完整运行（含 GitHub Actions 状态）
GITHUB_TOKEN=ghp_... python scripts/daily_check.py

# 带飞书告警
GITHUB_TOKEN=ghp_... ALERT_WEBHOOK=https://... python scripts/daily_check.py
```

输出为 Markdown 格式的巡检报告，含每项检查的 ✅/❌ 状态。

---

## 每日检查 (Daily)

**执行人**: Release Engineer
**时间预算**: < 15 分钟
**频率**: 每工作日

### 检查清单

| # | 检查项 | 工具 | 通过标准 | 严重性 |
|---|--------|------|----------|--------|
| 1 | GitHub Actions 最新 run 状态 | `daily_check.py` | 最近 5 次 run 全绿 | P1 |
| 2 | 站点健康检查 | `health_check.py` | 所有端点返回预期状态码 | P0 |
| 3 | SSL 证书有效期 | `daily_check.py` | 所有域名 > 30 天 | P1 |
| 4 | UptimeRobot 可用率 | UptimeRobot Dashboard | 30 天可用率 ≥ 99.5% | P1 |
| 5 | P0 反馈积压 | 反馈系统 | 无未处理 P0 超过 2 小时 | P0 |

### 操作步骤

1. 运行 `python scripts/daily_check.py`
2. 检查 UptimeRobot 面板确认可用率
3. 检查反馈系统确认无超时 P0
4. 如有失败项，按严重性处理并记录

### 故障处理

- **P0 失败**: 立即通知 CTO，30 分钟内响应
- **P1 失败**: 记录到当周周报，24 小时内处理

---

## 每周检查 (Weekly)

**执行人**: CPO
**时间预算**: < 30 分钟
**频率**: 每周一

### 检查清单

| # | 检查项 | 通过标准 | 严重性 |
|---|--------|----------|--------|
| 1 | E2E 测试 cron 结果 | 最近一周无新增失败 | P1 |
| 2 | 博客内容审核 | 无不当内容或过期信息 | P2 |
| 3 | 依赖安全审计 | `pnpm audit` 无 high/critical | P1 |
| 4 | Core Web Vitals | LCP < 2.5s, CLS < 0.1 | P1 |
| 5 | 本周反馈统计 | SLA 达标率 ≥ 95% | P1 |

### 操作步骤

1. 检查 GitHub Actions 中 E2E 测试 cron workflow 结果
2. 审核博客最新发布内容
3. 在项目根目录运行 `pnpm audit`
4. 使用 Lighthouse 或等效工具检查 Core Web Vitals
5. 填写周报（使用 [`templates/weekly.md`](templates/weekly.md)）

---

## 每月检查 (Monthly)

**执行人**: CTO + CPO
**时间预算**: < 1 小时
**频率**: 每月第一个工作日

### 检查清单

| # | 检查项 | 通过标准 | 严重性 |
|---|--------|----------|--------|
| 1 | 安全评估 | CORS/CSP 配置正确、无新漏洞 | P0 |
| 2 | 性能基线对比 | 与上月对比无退化 > 10% | P1 |
| 3 | 反馈数据统计 | 月度汇总、SLA 达标率 | P1 |
| 4 | 容量评估 | 数据库大小、API 调用量趋势 | P2 |

### 操作步骤

1. CTO 执行安全评估（CORS 策略、CSP headers、依赖漏洞扫描）
2. CPO 对比性能基线数据
3. 汇总当月反馈数据，计算 SLA 达标率
4. 评估数据库增长和 API 使用趋势
5. 填写月报（使用 [`templates/monthly.md`](templates/monthly.md)）

---

## 报告模板

| 模板 | 用途 | 位置 |
|------|------|------|
| 周报 | 每周运营巡检报告 | [`templates/weekly.md`](templates/weekly.md) |
| 月报 | 每月运营巡检报告 | [`templates/monthly.md`](templates/monthly.md) |

---

## SLA 目标

| 指标 | 目标 | 测量方式 |
|------|------|----------|
| 站点可用率 | ≥ 99.5% | UptimeRobot 30 天 |
| P0 反馈响应 | < 2 小时 | 反馈系统时间戳 |
| P1 反馈响应 | < 24 小时 | 反馈系统时间戳 |
| SLA 达标率 | ≥ 95% | 月度统计 |
| Core Web Vitals LCP | < 2.5s | Lighthouse |
| Core Web Vitals CLS | < 0.1 | Lighthouse |
