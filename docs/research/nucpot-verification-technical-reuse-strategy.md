# NucPot 势函数验证模块技术复用策略

> **文档类型**：Architecture Decision Record (ADR)
> **日期**：2026-05-26
> **状态**：已确认
> **关联文档**：`potential-quality-assessment-survey.md`（调研报告）

---

## 1. 核心原则

**让 OpenKIM 干脏活，NucPot 专注管线与业务。**

不在团队内部从零开发势函数文件解析器。复用 OpenKIM 的标准接口，将精力投入到高通量计算的并发调度与数据流转上。

---

## 2. 许可证基础

| 组件 | 许可证 | 复用方式 |
|------|--------|---------|
| kim-api | LGPL-2.1 | 动态链接，作为标准翻译层 |
| kimpy (Python 绑定) | LGPL-2.1 | pip install，Worker 层直接调用 |
| kimvv (Test Driver) | LGPL-2.1 | pip install，基础物理量计算 |
| kim-tools | LGPL-2.1 | pip install，辅助工具 |
| iprPy (NIST) | 美国公共领域 | 可自由复制、修改、分发 |
| KIM Developer Platform | 明大版权，as-is | Docker 容器可直接使用 |
| ClawTeam (HKUDS) | MIT | Agent 编排层，L3+ 阶段引入 |

LGPL-2.1 要求：kimvv/kimpy 以独立库动态链接（pip install），不将其源码复制进 NucPot 仓库；对库本身的修改需以 LGPL-2.1 发布（预期不需要修改）。ClawTeam MIT 许可证允许自由使用、修改和集成。

---

## 3. 架构设计

### 3.1 核心执行环境

在服务器计算环境（Ubuntu 22.04）下，通过包管理器安装 kim-api，作为所有势函数的标准翻译层，屏蔽各势函数在参数格式和单位上的差异。

### 3.2 后台服务与计算节点解耦

```
┌─────────────────────────────────────────────────┐
│                   NucPot 主服务                    │
│  Next.js + Supabase (现有架构)                      │
└──────────────┬──────────────────────────────────┘
               │ 上传/验证请求
               ▼
┌─────────────────────────────────────────────────┐
│            API 与调度层 (FastAPI + uvicorn)        │
│  - 接收验证请求                                    │
│  - 生成计算任务                                    │
│  - 任务队列管理                                    │
│  - 结果回写 Supabase                               │
│  知识产权：完全私有/闭源                             │
└──────────────┬──────────────────────────────────┘
               │ 任务分发
               ▼
┌─────────────────────────────────────────────────┐
│              执行层 (Python Worker)                │
│  pip install kimpy + kimvv + kim-tools            │
│  - 通过 kimpy 调用势函数（能量/力/应力）              │
│  - 无需每次启动 LAMMPS 进程                         │
│  - 纯 Python 工作流                                │
│  知识产权：LGPL（kimpy/kimvv 调用）+ 私有（工作流逻辑）│
└─────────────────────────────────────────────────┘
```

**关键设计决策**：通过 kimpy 将能量、力、应力的计算请求直接传递给底层势函数，避免每次验算启动 LAMMPS 进程。

---

## 4. 模块边界与知识产权归属

| 系统模块 | 建议方案 | 知识产权归属 |
|---------|---------|------------|
| 势函数标准化解析层 | 直接集成 kim-api 与 kimpy | LGPL（开源合规，直接调用） |
| 基础物理量计算脚本 | 复用 OpenKIM 公开 Test 脚本 / kimvv API 调用 | LGPL（库调用方式） |
| Web 网关与任务流转 | 自研（异步调度、容错、数据库交互） | **完全私有/闭源** |
| 高阶特征与复杂属性验算 | 自研（金属燃料热稳定性测试等） | **完全私有/闭源** |
| 沙盒隔离与自动化管线 | 自研（Docker 容器控制、资源配额） | **完全私有/闭源** |

---

## 5. Test Driver 公私分离策略

### 5.1 公用部分（OpenKIM 公开 Test 脚本）

合法下载并复用公开测试脚本，作为系统基础体检指标：

- `EquilibriumCrystalStructure` — 平衡晶体结构
- `ElasticConstantsCrystal` — 弹性常数
- `CrystalStructureAndEnergyVsPressure` — 压力-体积曲线
- `GroundStateCrystalStructure` — 基态晶体结构
- `VacancyFormationEnergyRelaxationVolumeCrystal` — 空位形成能

### 5.2 私有部分（核材料专用 Test Driver）

针对 U-Mo-Nb-Zr 等复杂多元合金体系，市面上没有现成测试集。自研专用私有 Test Driver：

- U-Zr/Pu-Zr 合金相稳定性测试
- 辐照损伤特性测试（PKA 阈值位移能）
- 高温热物理属性测试（热膨胀、比热容）
- 燃料-包壳相互作用测试

私有脚本只需符合 KIM 的输入输出格式规范，核心业务逻辑完全属于自有知识产权。

---

## 6. 技术栈选型

| 层次 | 技术 | 说明 |
|------|------|------|
| 势函数解析 | kim-api (C) + kimpy (Python) | LGPL，势函数标准接口 |
| 基础属性计算 | kimvv + kim-tools | LGPL，Test Driver 库 |
| 缺陷性质计算 | 参考 iprPy（公共领域） | 公共领域，可自由复用 |
| Agent 编排 | ClawTeam（L3+ 阶段引入） | MIT，多 Agent 协作框架 |
| API 网关 | FastAPI + uvicorn | MIT，异步调度 |
| 任务队列 | Celery / Redis / RQ | BSD，分布式任务 |
| 计算沙盒 | Docker 容器 | Apache 2.0，隔离执行 |
| 数据存储 | Supabase (PostgreSQL) | 与现有 NucPot 架构一致 |
| 前端展示 | Next.js (现有) | 与现有 NucPot 架构一致 |

---

## 7. 开发量估算

通过复用 OpenKIM 生态，预计可减少 60-70% 的开发量：

| 模块 | 从零开发 | 复用后 | 节省 |
|------|---------|-------|------|
| 势函数解析与标准化 | 3-4 月 | 2 周（集成 kim-api） | ~90% |
| 基础属性计算（5种） | 2-3 月 | 3-4 周（调用 kimvv） | ~70% |
| 缺陷性质计算（5种） | 2-3 月 | 4-6 周（参考 iprPy） | ~60% |
| 计算管线与调度 | 1-2 月 | 1-2 月（自研，不可复用） | 0% |
| 核材料专用验证 | 2-3 月 | 2-3 月（自研，不可复用） | 0% |
| **总计** | **10-15 月** | **4-6 月** | **~65%** |

---

## 8. Agentic 验证架构：从自动化流到 AI 科学家助手

传统的被动自动化测试流（上传 → 跑固定脚本 → 返回结果）无法应对核材料领域的复杂性：U-Mo-Nb-Zr 等多元体系缺乏标准测试库，物理量的选择需要领域判断，计算参数需要根据势函数特性动态调整。通过引入多智能体（Agentic）架构，让 Agents 充当 Orchestrator 自动编排甚至联合生成测试任务，将验证系统升级为具备认知与动态纠错能力的 AI 科学家助手。

### 8.1 四智能体协作架构

```
用户指令: "评估 U-Mo-Nb-Zr 势函数在 600K 下的相稳定性和基础力学性能"
                              │
                              ▼
                ┌─────────────────────────┐
                │   Orchestrator Agent     │
                │   （全局编排者）            │
                │   - 指令解析与任务拆解      │
                │   - 生成 DAG 任务图        │
                │   - 监控全局进度           │
                └─────────┬───────────────┘
                          │ DAG: 晶格弛豫→弹性张量→空位形成能→MD热浴
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
   │Test Generation│ │Test Generation│ │Test Generation│
   │  Agent (T1)   │ │  Agent (T2)   │ │  Agent (T3)   │
   │ 晶格弛豫脚本   │ │ 弹性张量脚本   │ │ 空位形成能脚本  │
   └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
          │                │                │
          ▼                ▼                ▼
   ┌─────────────────────────────────────────────┐
   │       Execution & Debugging Agent            │
   │       （执行与调试专员）                        │
   │  Docker 沙盒 → kimpy/LAMMPS → 错误检测         │
   │  ↺ 失败时反馈给 Generation Agent 修正参数       │
   └──────────────────┬──────────────────────────┘
                      │ 计算完成
                      ▼
   ┌─────────────────────────────────────────────┐
   │     Data Extraction & Analysis Agent          │
   │     （数据抽取与分析员）                        │
   │  - 从 Log/Dump/KIM-EDN 提取结构化数据           │
   │  - 与 DFT/实验参考值比对                        │
   │  - 生成验证结论与评分                            │
   └─────────────────────────────────────────────┘
```

### 8.2 各 Agent 职责定义

#### Orchestrator Agent（全局编排者）

**定位**：系统主控大脑，类似于 Paperclip 或 HyperAgents 的核心中枢。

**核心职责**：
- 接收用户自然语言指令，解析验证意图
- 根据势函数类型（EAM/MEAM/ML）、材料体系、温度条件等上下文，将任务拆解为有向无环图（DAG）格式的子任务序列
- 确定 DAG 中子任务的依赖关系和并行策略
- 监控各子任务执行状态，处理全局异常

**输出产物示例**：
```
任务 DAG: "评估 U-Mo-Nb-Zr 在 600K 下"
├── T1: BCC 晶格弛豫 (先决条件)
│   ├── T2: 弹性张量计算 (依赖 T1)
│   ├── T3: 空位形成能计算 (依赖 T1)
│   └── T4: 600K NPT MD 热浴测试 (依赖 T1)
└── T5: 相稳定性对比 (依赖 T2, T3, T4)
```

#### Test Generation Agent（测试代码生成器）

**定位**：根据编排者下发的子任务，生成可直接执行的 Python 脚本。

**核心职责**：
- 调用内部代码模板库，生成 kimpy 或 LAMMPS 可执行的脚本
- 理解 KIM API 调用规范，正确设置势函数参数
- 根据材料体系特性设置关键模拟参数（timestep、截断半径、k-point 等）
- 针对缺乏标准测试库的多元体系，根据文献逻辑自主推演需要验证的物理量

**关键能力**：
- 深刻理解 KIM API 调用规范
- 掌握多元合金体系的特定参数设置
- 能够从文献中提取验证逻辑并转化为可执行代码

**输入输出契约**：
```python
# 输入
{
    "task_type": "elastic_constants",
    "potential": "EAM_Dynamo_ZhouJW_2004_U__MO_000000000000",
    "species": ["U", "Mo", "Nb", "Zr"],
    "structure": "BCC",
    "temperature": 600,  # K
    "constraints": {"timestep": 0.001, "cutoff": 8.0}
}

# 输出：可执行的 Python 脚本
```

#### Execution & Debugging Agent（执行与调试专员）

**定位**：沙盒内的计算执行者，具备自治调试能力。

**核心职责**：
- 在隔离的 Docker 沙盒中触发代码执行
- 实时监控计算状态（能量收敛、温度漂移、原子丢失等）
- 执行失败时提取报错日志和最后几步的热力学输出

**闭环反馈机制（Multi-Agent Debate/Critic）**：

常见故障模式与自治修正策略：

| 故障现象 | 根因分析 | 修正策略 |
|---------|---------|--------|
| `Lost atoms` | timestep 过大 / 近距离势能发散 | 减小 timestep，增加 neighbor list 更新频率 |
| `Temperature out of range` | 热浴耦合参数不当 | 调整 thermostat 阻尼系数 |
| 能量不收敛 | 结构不合理 / 势函数外推 | 切换到更保守的弛豫策略（fire → cg）|
| 残余力过大 | 盒子尺寸不合适 | 先 relax box 再 relax atoms |

修正后脚本反馈给 Generation Agent 重新生成，形成自治 Debug 循环。设定最大重试次数（建议 3 次）防止死循环。

#### Data Extraction & Analysis Agent（数据抽取与分析员）

**定位**：从原始计算输出中提取结构化验证结论。

**核心职责**：
- 从 LAMMPS log、dump 文件、KIM-EDN 格式输出中精准提取弹性模量、能量差等结构化数据
- 与 Supabase 数据库中的 DFT/实验参考值进行自动比对
- 计算误差指标（AE、RE、MAE）和等级评定（A-F）
- 生成人类可读的验证结论报告

**输出格式示例**：
```json
{
  "potential_id": "...",
  "verification_date": "2026-05-26T21:44:00Z",
  "results": [
    {
      "property": "lattice_constant",
      "unit": "Å",
      "computed": 3.42,
      "reference": 3.38,
      "relative_error_pct": 1.18,
      "grade": "A"
    },
    {
      "property": "C11",
      "unit": "GPa",
      "computed": 185.3,
      "reference": 192.0,
      "relative_error_pct": 3.49,
      "grade": "C"
    }
  ],
  "overall_grade": "B",
  "summary": "晶格常数预测良好，弹性常数 C11 偏差 3.5%，建议用于结构模拟但谨慎用于力学分析"
}
```

### 8.3 智能体协作的通信协议

各 Agent 之间的通信采用结构化 JSON 消息，通过任务队列（Redis/Celery）传递：

```
Orchestrator ──(task_dispatch)──→ Generation
Generation   ──(code_ready)───→ Execution
Execution    ──(exec_result)───→ Analysis
Execution    ──(exec_failure)──→ Generation  [Debug 循环]
Analysis     ──(verification_done)→ Orchestrator [任务完成]
Orchestrator ──(dag_complete)──→ 用户 [最终报告]
```

### 8.4 与技术复用架构的整合

Agentic 层运行在技术复用策略定义的基础设施之上：

| Agentic 层 | 底层依赖 | 说明 |
|-----------|---------|------|
| Orchestrator | FastAPI 调度层 | DAG 编译为 Celery 任务链 |
| Generation Agent | kimpy/kimvv API 规范 | 生成符合 KIM 接口的脚本 |
| Execution Agent | kim-api + Docker 沙盒 | 沙盒内 kim-api 执行势函数 |
| Analysis Agent | Supabase 参考数据库 | 比对结果写入 Supabase |

### 8.5 从被动管线到认知系统的演进路线

| 阶段 | 能力 | 实现复杂度 |
|------|------|----------|
| **L1: 被动管线** | 固定模板 → kimvv 执行 → 结果回写 | 低 |
| **L2: 参数化管线** | 用户选验证项 → 模板填充参数 → 执行 | 中低 |
| **L3: 编排型智能体** | 自然语言指令 → DAG 拆解 → 多任务编排 | 中 |
| **L4: 认知型智能体** | L3 + 自治 Debug + 文献推理 + 动态生成 | 高 |

建议从 L1 开始，逐步演进。L1-L2 可在 Phase 1（MVP）中实现；L3 在 Phase 2 中实现；L4 在 Phase 3 中实现。

### 8.6 Agent 编排框架选型：ClawTeam 评估结论

经过对 [ClawTeam](https://github.com/HKUDS/ClawTeam)（港大数据科学实验室 HKUDS，MIT 许可证）的评估，结论如下：

**ClawTeam 的核心能力**：通过 CLI 编排多个 AI Agent 协作——Leader Agent 自动拆解任务、Spawn Worker Agents（每个有独立 git worktree + tmux 窗口）、通过 inbox 文件消息队列协调、Board 看板监控。支持 Claude Code / Codex / OpenClaw 等任意 CLI Agent 作为 Worker。

**匹配度分析**：

| NucPot 需求 | ClawTeam 能力 | 匹配度 |
|------------|-------------|--------|
| Orchestrator Agent（指令解析 → DAG） | Leader Agent + `task create --blocked-by` 依赖链 | ✅ 高 |
| Test Generation Agent（生成脚本） | Worker Agent 可接收任务描述并自主生成代码 | ✅ 高 |
| Execution Agent（沙盒执行 + Debug 循环） | Worker 在独立 worktree 中执行；但无内置沙盒隔离，无自治 Debug 循环 | ⚠️ 中 |
| Analysis Agent（数据提取 + 对比评分） | Worker 可完成，但需自行实现解析逻辑 | ⚠️ 中 |
| Docker 沙盒资源隔离 | 仅 git worktree + tmux，不支持容器 | ❌ 不匹配 |
| GPU/HPC 资源调度 | 无内置资源管理 | ❌ 不匹配 |
| 科学计算领域特化 | 通用框架，无领域知识 | ❌ 不匹配 |

**核心判断**：ClawTeam 的优势在于 **Agent 编排层**（谁做什么、什么顺序、怎么通信），而非计算执行层（怎么跑 LAMMPS、怎么管 Docker）。

**决策：分阶段引入，编排层复用 + 执行层自研**

```
┌─────────────────────────────────────────────────┐
│        ClawTeam 编排层 (MIT 许可证)                │
│  [L3+ 阶段引入]                                   │
│  Leader Agent = Orchestrator                      │
│  Worker Agent = Test Generation / Execution /     │
│                 Analysis                          │
│  inbox 消息队列 + task DAG + board 监控            │
│  通过 --json CLI 输出与下层对接                      │
└──────────────┬──────────────────────────────────┘
               │ CLI / JSON 接口
               ▼
┌─────────────────────────────────────────────────┐
│         NucPot 自研执行层 (完全私有)                │
│  [L1 起持续建设]                                   │
│  - FastAPI 网关 + Celery 任务队列                   │
│  - Docker 沙盒（资源隔离与配额）                      │
│  - kimpy / kimvv / LAMMPS 计算引擎                  │
│  - Supabase 数据存储与参考值比对                      │
│  - 自治 Debug 循环逻辑（失败→分析→修正→重试）         │
│  - 核材料领域知识模板库                              │
└─────────────────────────────────────────────────┘
```

**各阶段与 ClawTeam 的关系**：

| 阶段 | ClawTeam 角色 | 自研重点 |
|------|-------------|---------|
| L1: 被动管线 | **不引入** | FastAPI + Celery + kimvv 固定模板 |
| L2: 参数化管线 | **不引入** | 模板参数化 + 用户选验证项 |
| L3: 编排型智能体 | **引入**：ClawTeam 负责 Agent 编排、任务 DAG、inbox 通信 | Docker 沙盒 + Debug 循环 + 领域模板 |
| L4: 认知型智能体 | **深化使用**：Leader Agent 具备文献推理能力 | 文献驱动的动态测试生成 |

**ClawTeam 的具体复用方式（L3+）**：

1. **Agent 拓扑管理**：`clawteam team spawn-team` 创建验证团队，Leader 自动承担 Orchestrator 角色
2. **任务 DAG**：`clawteam task create --blocked-by` 直接映射到验证任务的依赖关系（如弹性常数依赖先完成晶格弛豫）
3. **Agent 间通信**：`clawteam inbox send/receive` 实现 Orchestrator ↔ Generation ↔ Execution ↔ Analysis 的消息传递
4. **进度监控**：`clawteam board show/live` 提供验证进度的实时看板
5. **git worktree 隔离**：每个 Worker Agent 的验证脚本在独立分支上开发，便于版本管理和审查
6. **`--json` 输出**：所有命令支持 JSON 格式，与 NucPot FastAPI 管线无缝对接

**需要自研补充的 ClawTeam 缺失能力**：

- Docker 沙盒执行环境（ClawTeam 仅 tmux 隔离，势函数计算需要资源配额限制）
- Execution Agent 的自治 Debug 循环（ClawTeam Worker 一次性执行，无"失败→分析→修正→重试"闭环）
- 科学计算后处理（KIM-EDN / LAMMPS log 解析、误差评分、A-F 等级）
- GPU/HPC 资源调度
- 核材料领域知识注入

---

## 9. 后续行动

- [ ] 在计算服务器上搭建 kim-api + kimpy + kimvv 环境
- [ ] 实现验证 FastAPI 网关原型
- [ ] 测试 kimvv 对核材料势函数（EAM/MEAM）的兼容性
- [ ] 设计 NucPot 私有 Test Driver 的输入输出格式规范
- [ ] 编写核材料专用验证模块的需求规格
- [ ] 设计 Agentic 架构的 Agent 通信协议和消息格式
- [ ] 实现 Orchestrator Agent 的 DAG 编排逻辑（L3 阶段）
- [ ] 构建 Test Generation Agent 的代码模板库
- [ ] L3 阶段前完成 ClawTeam 安装验证与 OpenClaw 集成测试
- [ ] 设计 ClawTeam Worker 与 NucPot Docker 沙盒的对接方案

---

*本文档基于 2026-05-26 调研报告和技术讨论整理，由用户确认后归档。*
