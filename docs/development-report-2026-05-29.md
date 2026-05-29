# NucPot 开发测试报告

**报告日期**: 2026-05-29  
**项目**: NucPot — 核材料原子间势函数开放平台  
**仓库**: GitHub Etoile04/nucpot  
**线上地址**: https://nucpot.dpdns.org  

---

## 一、项目概览

NucPot 是面向核燃料、包壳和结构材料的原子间势函数存储、检索、验证与共享平台。前端基于 Next.js 15 (App Router) + Tailwind CSS，部署于 Vercel（Cloudflare CDN）；后端使用 Supabase Cloud；验证服务基于 Python FastAPI + LAMMPS，运行于 ThinkStation，通过 Cloudflare Named Tunnel 暴露。

### 技术架构

```
用户浏览器
  ├── https://nucpot.dpdns.org → Cloudflare CDN → Vercel (Next.js 前端)
  │     └── Supabase Cloud (数据库 + Auth + Storage)
  └── https://verify.nucpot.dpdns.org → Cloudflare Named Tunnel → ThinkStation:8001 (验证服务)
       └── LAMMPS (分子动力学计算)
       └── Supabase REST API (势函数元数据 + 结果回写)
```

### 当前数据规模

| 指标 | 数值 |
|------|------|
| 已发布势函数 | 54 |
| 已验证势函数 | 9/54 (16.7%) |
| 注册用户 | 1 |
| 前端 TypeScript 文件 | 49 个，6,837 行 |
| 验证服务 Python 文件 | 25 个，2,552 行 |
| API 端点 | 18 个 |
| 页面路由 | 12 个 |

---

## 二、开发时间线

### 第一阶段：基础功能开发 (05-25 ~ 05-26)

**目标**: 完成前端核心页面和基础 API

**主要工作**:
- Next.js 15 项目搭建（App Router + Turbopack）
- Supabase Cloud 数据库 + 认证配置
- 50 条种子势函数数据导入
- 核心页面：首页、浏览、搜索、详情、登录/注册
- 基础 API：势函数 CRUD、认证、统计

**产出**: 33 commits

---

### 第二阶段：功能迭代 (05-26 ~ 05-27 早期)

**目标**: 根据用户测试反馈进行 3 轮迭代优化

#### Iteration 1 — P0 修复
- 浏览/搜索页分页 UI（API 已支持）
- 元素筛选器从 7 个硬编码 → API 动态获取全部 24 种元素

#### Iteration 2 — P1 功能
- `verified_props` 结构化展示（替代 JSON.stringify）
- 势函数对比功能（/compare）
- 完整上传表单（高级选项）
- 错误/加载页面 + 移动端响应式
- 贡献审核状态展示

#### Iteration 3 — P2 功能
- LAMMPS 输入脚本模板生成
- 温度范围过滤 + 排序 + 搜索高亮
- SEO 基础 + 无障碍改进

**产出**: 3 个 merge commit（iteration 1/2/3），功能基本完整

---

### 第三阶段：验证管线 + 云部署 (05-27)

**工作量**: 27 commits，6,712 行新代码，16 小时

#### 3.1 验证管线开发 (05:00 ~ 08:00)
- nucpot-autovc Phase 2：参数化验证管线
- 4 种验证模板（basic / mechanical / defect / comprehensive）
- 5 种计算属性（晶格常数、结合能、弹性常数、体模量、空位形成能）
- 78/78 tests 通过
- Dockerfile + docker-compose 部署配置

#### 3.2 云部署 (08:00 ~ 11:30)
- Vercel 前端部署
- ThinkStation FastAPI 服务启动 (port 8001)
- Cloudflare Quick Tunnel 暴露验证服务
- 端到端验证通过

#### 3.3 验证 404 错误修复 (11:30 ~ 12:00)
- **问题**: 验证服务 SQLite 只有 2 条数据，Supabase 有 44 条，导致找不到势函数
- **修复**: 自动创建缺失记录
- **深层问题**: 验证服务与 Supabase 数据不同步

#### 3.4 架构重构 (12:00 ~ 18:00)
- **关键决策**: 验证服务从 SQLite 切换到直接读 Supabase
- LAMMPS 安装（ThinkStation conda，`lmp_serial`）
- `supabase_client.py` — 从 Supabase REST API 获取势函数元数据
- `lammps_runner.py` — LAMMPS 计算后端
- 管理后台验证页面（/admin/verify）
- 详情页改为只读验证结果展示
- 91 tests 全部通过

#### 3.5 域名配置 (18:00 ~ 21:30)
- DigitalPlat FreeDomain 注册 `nucpot.dpdns.org`
- Cloudflare DNS → Vercel 绑定
- Named Tunnel `nucpot-verify` 持久化
- `verify.nucpot.dpdns.org` → ThinkStation:8001

---

### 第四阶段：基础设施完善 (05-28 ~ 05-29)

#### 05-28
- Supabase Storage 配置（势函数文件上传）
- 势函数文件批量上传（HNU FeCrAl, WTaVCr, U-MTP）
- 相对 Storage URL（环境无关）
- 密码修改、意见反馈、关于页更新
- 反馈表数据库迁移

#### 05-29
- 验证结果回写 `potentials.verified_props`（Format B）
- 前端兼容 API 路由（验证服务 → 前端格式转换）
- **关键 Bug 修复**: TypeScript 类型错误阻塞 Vercel 构建
  - 根因: `typeof results` 包含 `undefined`，`[]` 无法赋值
  - 影响: 28 小时线上版本未更新，验证结果无法显示
  - 修复: 显式 `Array<{...}>` 类型标注

---

## 三、当前功能清单

### 前端页面

| 页面 | 路由 | 功能 |
|------|------|------|
| 首页 | `/` | 搜索框、热门标签、统计 |
| 浏览 | `/browse` | 分页、元素筛选、排序 |
| 高级检索 | `/search` | 全文搜索、温度范围、高亮 |
| 详情 | `/potential/[id]` | 元数据、验证结果、LAMMPS 模板、文件下载 |
| 对比 | `/compare` | 多势函数并排对比 |
| 上传 | `/upload` | 贡献表单（含高级选项） |
| 登录/注册 | `/login` | 邮箱+密码认证 |
| 个人资料 | `/profile` | 编辑资料、贡献审核状态 |
| 反馈 | `/feedback` | 意见提交 |
| 关于 | `/about` | 平台介绍 |
| 管理后台 | `/admin/verify` | 势函数验证管理 |

### API 端点

| 类别 | 端点数 | 说明 |
|------|--------|------|
| 势函数 | 4 | 列表、详情、上传、文件上传 |
| 认证 | 5 | 登录、注册、资料、模板、贡献 |
| 管理 | 2 | 统计、贡献审核 |
| 验证 | 5 | 模板、提交、查询、报告 |
| 其他 | 2 | 统计、反馈 |
| **合计** | **18** | |

### 验证服务能力

| 属性 | 说明 |
|------|------|
| 晶格常数 (lattice_constant) | LAMMPS 能量最小化 |
| 结合能 (cohesive_energy) | 原子能量差 |
| 弹性常数 (elastic_constant) | 应变-应力关系 |
| 体模量 (bulk_modulus) | 体积弹性响应 |
| 空位形成能 (vacancy_formation_energy) | 缺陷能量 |

---

## 四、测试记录

### 4.1 功能测试

| 测试项 | 日期 | 结果 |
|--------|------|------|
| 注册登录流程 | 05-26 | ✅ 角色隔离正确 |
| 浏览页分页 | 05-27 | ✅ 50 条数据正常分页 |
| 搜索页高亮 | 05-27 | ✅ 关键词高亮显示 |
| 势函数对比 | 05-27 | ✅ 多选对比正常 |
| 验证模板获取 | 05-27 | ✅ 4 模板返回正确 |
| 验证提交 | 05-27 | ✅ 创建任务 → 计算 → 回写 |
| 进度查询 | 05-27 | ✅ 实时进度 + 预估时间 |
| CORS 跨域 | 05-27 | ✅ Cloudflare Tunnel 配置正确 |
| 文件上传 | 05-28 | ✅ Supabase Storage 正常 |
| 验证结果展示 | 05-29 | ✅ Format B 自动转换 |

### 4.2 验证管线测试

| 测试类型 | 数量 | 状态 |
|----------|------|------|
| nucpot-autovc 单元测试 | 78 | ✅ 全部通过 |
| 重构后测试 | 91 | ✅ 全部通过 |
| 端到端验证 | 1 | ✅ 提交→计算→回写完整链路 |

### 4.3 性能测试

| 指标 | 结果 |
|------|------|
| 首页加载 (Vercel CDN) | ~1.7s |
| API 响应 (/api/potentials) | ~2.9s |
| 验证服务健康检查 | <100ms |
| Vercel 构建时间 | ~60s |

---

## 五、经验教训

### 5.1 技术教训

| # | 教训 | 详情 | 影响 |
|---|------|------|------|
| 1 | **先分析数据再选技术栈** | 初期用 KIM API + SQLite，但 Supabase 中 0 个 KIM 格式、49 个 EAM/LAMMPS 格式，导致后期重构 | 延误 ~4h |
| 2 | **本地构建成功 ≠ 部署成功** | commit 1224ca0 有 TS 类型错误，本地未检测到（`npm run build` 未运行），Vercel 构建静默失败，线上版本停更 28 小时 | 验证结果不显示 |
| 3 | **部署后必须验证** | 多次认为"已修复"但实际 Vercel 缓存/构建失败导致旧版本在线上运行 | 用户反复反馈问题 |
| 4 | **`typeof` 类型推导陷阱** | `typeof results` 对联合类型推导为完整联合，`const x: typeof y = []` 当 `y: T | undefined` 时类型错误 | 构建失败 |
| 5 | **Quick Tunnel vs Named Tunnel** | Quick Tunnel URL 每次重启都变，不应用于任何需要持久化的场景 | 多走一步 |

### 5.2 流程教训

| # | 教训 | 改进措施 |
|---|------|----------|
| 1 | **每次 commit 后应 `npm run build`** | 加入 git pre-push hook 或 CI 检查 |
| 2 | **部署后检查 Vercel 构建状态** | push 后等 2 分钟，curl 检查 age header |
| 3 | **conda 大包安装设定长超时** | LAMMPS conda install 15 分钟，不应频繁 poll |
| 4 | **Vercel CLI 需提前登录** | 每次需要手动去 Dashboard 操作，浪费时间 |

### 5.3 架构决策复盘

| 决策 | 正确性 | 说明 |
|------|--------|------|
| Next.js App Router | ✅ | SSR + CSR 混合，部署简单 |
| Supabase Cloud | ✅ | 免费层够用，RLS 安全 |
| Vercel 部署 | ✅ | GitHub 自动集成，但构建失败静默 |
| Cloudflare Tunnel | ✅ | Named Tunnel 稳定，零成本暴露本地服务 |
| SQLite → Supabase | ✅ | 消除数据不一致 |
| KIM API → LAMMPS | ✅ | 匹配实际数据格式 |

---

## 六、路线图执行情况

### Phase 1：基础平台 ✅ 已完成
- [x] 核心页面（首页、浏览、搜索、详情）
- [x] Supabase 数据库 + 认证
- [x] 种子数据导入
- [x] 3 轮功能迭代（P0/P1/P2）

### Phase 2：验证管线 ✅ 已完成
- [x] 验证服务（nucpot-autovc）
- [x] LAMMPS 计算后端
- [x] 4 种验证模板
- [x] 5 种计算属性
- [x] 管理后台验证页面
- [x] 详情页验证结果展示
- [x] 进度条 + 预估时间

### Phase 3：云部署 ✅ 已完成
- [x] Vercel 前端部署
- [x] Cloudflare CDN + DNS
- [x] Named Tunnel 持久化
- [x] 正式域名 nucpot.dpdns.org
- [x] Supabase Storage 文件上传

### Phase 4：待完成 🔲
- [ ] Named Tunnel 开机自启动（systemd service）
- [ ] 验证服务进程管理（systemd / supervisord）
- [ ] 参考值数据完善（REFERENCE_VALUES 扩展）
- [ ] 更多势函数文件上传（当前仅 3 个有文件）
- [ ] 前端 E2E 自动化测试
- [ ] CI/CD 管线（pre-push build check）
- [ ] Cloudflare API Token 安全存储
- [ ] 移动端管理后台适配

---

## 七、关键配置索引

| 配置项 | 值 |
|--------|-----|
| 前端域名 | `https://nucpot.dpdns.org` |
| 验证服务域名 | `https://verify.nucpot.dpdns.org` |
| Cloudflare Tunnel | `nucpot-verify` (b6872742-...) |
| Vercel 项目 | Etoile04/nucpot (GitHub auto-deploy) |
| ThinkStation SSH | `z203@100.70.30.21` |
| LAMMPS 路径 | `~/anaconda3/bin/lmp_serial` |
| Supabase URL | Cloud (通过 NEXT_PUBLIC_SUPABASE_URL) |
| 前端环境变量 | `NEXT_PUBLIC_AUTOCV_API_URL=https://verify.nucpot.dpdns.org` |

---

## 八、下一步建议

1. **基础设施稳定性**：将 Named Tunnel 和验证服务配置为 systemd service，确保重启后自动恢复
2. **CI/CD**：添加 GitHub Actions（build check + lint），防止 TS 错误再次阻塞部署
3. **数据填充**：上传更多势函数文件（.eam.alloy 等），使验证管线可实际计算
4. **参考值扩展**：将 REFERENCE_VALUES 从硬编码扩展到 Supabase 配置表
5. **自动化测试**：前端 vitest + Playwright E2E 测试

---

*报告生成时间: 2026-05-29 14:50 CST*  
*总开发周期: 2026-05-25 ~ 2026-05-29 (5 天)*  
*总 commits: 82 (其中 49 个在 05-25 之后)*
