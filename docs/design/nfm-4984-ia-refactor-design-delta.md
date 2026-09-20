# NFM-4984 IA 重构 Delta 设计规范

> **Author:** UXDesigner · **Date:** 2026-09-20 · **Issue:** NFM-4985 · **Parent:** NFM-4984
> **Status:** 定稿待 CPO 签收后转入实现
> **基线:** NFM-1064《NFMD 前端网站交互设计规范》§2–§6 + 附录 A/B/C（origin/main，已批准）
> **IA 裁决来源:** CPO 裁决 NFM-4984；站点地图以 `platform-design.md §站点地图` 为准
> **证据:** UAT-1（一级导航信息过载）；09-04 功能报告 §5 差距 #1（首页五要素全缺失）

---

## 0. 范围与阅读方式

本文件是 **delta 规范**，只定义相对 NFM-1064 基线的变更，不重复基线内容：

| 主题 | 权威来源 | 本文件职责 |
|------|----------|-----------|
| 设计系统 Token / 组件优先级 / 禁项 | NFM-1064 §2 | 仅做合规声明（§4） |
| 页面交互、响应式、状态、无障碍 | NFM-1064 §3–§6 | 沿用；仅导航与首页有 delta（§1、§2） |
| 站点地图 / 信息架构 | platform-design.md §站点地图 | 落实为导航定稿 + 路由表（§1、§3） |
| NFM-1064 §1.1 站点地图 | — | **作废**，以本文件 §1/§3 为准 |

实现顺序建议：§3 路由与重定向 → §1 导航栏 → §2 首页五要素。§3 先行可让 §1/§2 的新链接一次性指向最终路径。

---

## 1. 导航栏定稿结构

### 1.1 一级导航（唯一合法清单，≤5 项）

| 顺序 | 标签 | 路由 | 说明 |
|---|---|---|---|
| 1 | 势函数库 | `/potentials` | 唯一带二级导航的一级项（见 1.2） |
| 2 | 材料体系 | `/materials` | 直达，无二级 |
| 3 | 文献库 | `/literature` | 直达，无二级 |
| 4 | 博客 | `/blog` | 直达，无二级 |
| 5 | 关于 | `/about` | 直达，无二级 |

**硬性禁项（UAT-1 裁决）：**「浏览」「高级检索」「对比」不得出现在一级导航。三者分别收敛为：`/browse`→`/potentials`（重定向）、`/search`（势函数库二级）、`/compare`→`/potentials/compare`（势函数库二级）。

现状 `SiteHeader.tsx`（origin/main）一级共 7 项（浏览/本体/高级检索/对比/反馈/关于/博客），全部需按下表迁移。

### 1.2 势函数库二级导航 —— 交互形态裁决

**定稿：桌面端（≥`md`）采用 `Antd Dropdown` 挂在「势函数库」一级项上；不采用列表页内 Tab/侧栏承载二级。**

| 二级项 | 路由 |
|---|---|
| 势函数列表 | `/potentials` |
| 高级检索 | `/search` |
| 势函数对比 | `/potentials/compare` |

**裁决理由：**

1. **全局可达性**：检索与对比是跨页面高频动作（NFM-1064 §1.2 旅程 A/B 均从任意页发起）。若二级藏在 `/potentials` 页内 Tab，用户从 `/materials` 或 `/literature` 发起对比必须多跳一次，违背旅程 B「搜索 → 筛选 → 对比」的连续性。
2. **URL 语义**：`/search` 与 `/potentials/compare` 是独立路由（§3 裁决）。用页内 Tab 承载独立路由会造成「Tab 即路由」的面包屑/后退/分享混乱（Tab 切换不进历史栈，路由会进）。
3. **一级配额**：一级只有 5 项，二级仅有 3 项且稳定（platform-design 站点地图中势函数库子节点恰为 列表/详情/对比 + 检索降级），Dropdown 是承载「少而稳定」子项的最低成本形态，且 Antd Dropdown 已在组件优先级清单内（NFM-1064 §2.2 第 1 条）。
4. **放弃页内 Tab 的代价可接受**：`/potentials` 列表页自身的筛选/排序/视图切换（NFM-1064 §3.2）不与二级导航混淆，页面结构零改动，降低实现风险。

### 1.3 二级导航交互规格

| 维度 | 规格 |
|---|---|
| 触发（指针） | `trigger={['hover', 'click']}`：hover 即开（`mouseEnterDelay` 用 Antd 默认），点击亦开（触屏/确定性）。指针离开菜单与触发器后关闭（Antd 默认行为） |
| 触发（键盘） | 触发器是 `<Link href="/potentials">` 包裹的按钮语义：`Enter`/`Space` 展开菜单并将焦点移入第一项；再按 `Enter` 于聚焦项时跳转 |
| 菜单内键盘 | `↑`/`↓` 在 3 个菜单项间循环移动焦点；`Esc` 关闭菜单并把焦点还给「势函数库」触发器（焦点归还，NFM-1064 §6.1 键盘导航要求） |
| ARIA | 触发器 `aria-haspopup="menu"`、`aria-expanded` 随开合同步；菜单容器 `role="menu"`，菜单项 `role="menuitem"`（Antd Dropdown 默认输出，验收时以 DOM 断言核对） |
| 菜单项 | 每项都是真实 `<a href>`（Antd `menu` 配 `items` + Link），可中键新开、可被爬虫抓取；禁止 onClick 路由跳转的假链接 |
| 展开方向 | `placement="bottomLeft"`，与触发器左对齐 |
| 激活态（wayfinding） | 当前路由命中 `/potentials`、`/potentials/*`、`/search`、`/compare`（重定向前过渡期）任一前缀时，「势函数库」一级项显示激活态：文字 `var(--color-accent)` + 底部 2px 指示条（背景 `var(--color-accent)`） |
| 样式 | 菜单背景 `var(--color-surface)`、边框 `var(--color-border)`、菜单项文字 `var(--color-text-secondary)`、hover 项背景 `var(--color-border)`（半透明遮罩层用现有 token，不新造色值）；菜单项高度满足 ≥44px 触控区（§4.3） |

### 1.4 其余现存入口安置表（一级之外的全部去向）

| 现入口 | 去向 | 落位 | 理由 |
|---|---|---|---|
| 本体 `/ontology` | 页脚「数据服务」组 | Footer | 研究者低频深水区功能；platform-design 站点地图未列其为一级；页脚全站可达保证可达性不打折 |
| 知识图谱 `/kg` | 页脚「数据服务」组 | Footer | 同上；与本体同属知识组织工具，语义上不属于任何一级域 |
| 上传 `/upload` | **页头动作区**（登录按钮左侧） | Header actions | 贡献者旅程 C（NFM-1064 §1.2）的第一动作，属「动作」而非「内容域」，放一级会挤占 5 项配额；未登录点击跳 `/login?next=/upload` |
| 反馈 `/feedback` | 页脚「帮助与反馈」组 | Footer | 低频、单向、无回流动作；页脚常驻即可 |
| 登录态 | **页头动作区**：未登录 = 「登录」文字按钮（`Antd Button type="text"`）；已登录 = 头像 `Antd Dropdown`（个人中心 / 退出） | Header actions | 身份是动作不是目的地；头像下拉项均为真实链接 |

页脚信息架构（本次定稿，4 组）：

```
Footer
├── 平台: 关于 /about · 博客 /blog
├── 数据服务: 势函数库 /potentials · 材料体系 /materials · 文献库 /literature · 本体 /ontology · 知识图谱 /kg
├── 帮助与反馈: 反馈 /feedback · 使用指南(/blog tag)
└── 法务/版权行（现状保留）
```

### 1.5 `< md` 断点：汉堡菜单 + Drawer（NFM-1064 §4.2 导航栏行）

| 维度 | 规格 |
|---|---|
| 触发 | `< md` 隐藏一级横排，右侧显示汉堡 `Antd Button type="text" icon={<MenuOutlined/>}`，`aria-label="打开导航"`，`aria-expanded` 同步 |
| 容器 | `Antd Drawer placement="right" width="80vw"`（上限 `320px` 用 `min()` 表达于样式层，不硬编码新 token）；背景 `var(--color-surface)`、分割线 `var(--color-border)` |
| 内容结构（自上而下） | ① 一级 5 项（纯链接列表）② 「势函数库」以 `Antd Menu` 内联展开子项（列表/高级检索/对比，`inline` 模式平铺，**禁止嵌套飞出**）③ 分割线 ④ 动作区：上传、登录/头像+退出 —— 固定于 Drawer 底部 |
| 键盘/焦点 | Drawer 打开后焦点移入首个菜单项；`Tab` 在 Drawer 内循环（焦点陷阱）；`Esc` 关闭并归还焦点给汉堡按钮 |
| 触控 | 所有菜单项行高 ≥44px（§4.3）；二级子项缩进用 `--wizard-padding-sm` |
| 状态记忆 | 当前路由对应项高亮（同 1.3 激活态规则），二级命中时同时展开父项 |

### 1.6 SiteHeader 改造要点

- 保持 sticky、`background: var(--color-surface)`、`border-bottom: 1px solid var(--color-border)`（现状已符合，保留）。
- 现状 inline style 中的 `0.75rem 1.5rem`、`1.125rem`、`0.9375rem`、`gap: 1.5rem` 等硬编码值，迁移时改引 NFM-1064 §2.1 token 或 Tailwind spacing scale（`px-6 py-3` / `text-lg` / `gap-6` 级别），**不得原样带入新导航**（§4 合规）。
- Logo/站名「核燃料与材料物性数据库」保留为回到 `/` 的链接。

---

## 2. 首页五要素布局

### 2.0 现状 → 目标

09-04 功能报告 §5 差距 #1 证据：设计要求的搜索框、统计数据区、热门势函数区、最新更新区、体系导航区「全部缺失，当前首页仅 Hero + 博客文章列表」。本节将 platform-design.md §核心页面线框·首页 的五要素落实为组件级规格。

### 2.1 布局图（桌面 ≥`lg`；`md` 同构收窄，`< md` 见 2.3）

```
┌──────────────────────────────────────────────────────────────┐
│ Navbar: 站名 | 势函数库▾ 材料体系 文献库 博客 关于 | [上传][登录]│
├──────────────────────────────────────────────────────────────┤
│ ① Hero                                                       │
│   平台定位标语（中/英，现状保留）                                │
│   [████████████████ 搜索材料 / 势函数 / 分子式...        🔍]   │
│   快捷 chips: [EAM] [MEAM] [MTP] [ACE] [U-Mo] [UO₂]          │
├──────────────────────────────────────────────────────────────┤
│ ② 统计数据区（StatCard ×4）                                    │
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐                  │
│ │势函数总数│ │材料体系数│ │文献条目 │ │属性记录 │                  │
│ └────────┘ └────────┘ └────────┘ └────────┘                  │
├──────────────────────────────────────────────────────────────┤
│ ③ 热门势函数区  标题 + 「查看全部 →」(/potentials)              │
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                          │
│ │Card×8 (PotentialCard 复用, 2行×4列@lg)│                      │
│ └──────┘ └──────┘ └──────┘ └──────┘                          │
├──────────────────────────────────────────────────────────────┤
│ ④ 最新更新区（List, 两列@lg）                                  │
│   [势函数] U-Mo Xe 修正 v2.3 · 2026-09-18                     │
│   [势函数] UO₂ ACE 新发布 · 2026-09-15 …                      │
├──────────────────────────────────────────────────────────────┤
│ ⑤ 体系导航区（4 组卡片）                                        │
│ ┌─金属燃料─┐ ┌─氧化物燃料─┐ ┌─包壳────┐ ┌─裂变气体──┐          │
│ │U U-Mo U-Zr│ │UO₂        │ │Zr Zr-Nb  │ │Xe Kr He   │          │
│ │          │ │           │ │Fe-Cr-Ni  │ │in matrix  │          │
│ └─────────┘ └───────────┘ └──────────┘ └──────────┘          │
├──────────────────────────────────────────────────────────────┤
│ ⑥ 最新文章（现状博客列表保留，BlogCard 复用，3列）               │
├──────────────────────────────────────────────────────────────┤
│ Footer                                                        │
└──────────────────────────────────────────────────────────────┘
```

顺序依据 platform-design 线框（Hero→统计→热门→最新更新→体系导航）。⑥ 为现状保留段（NFM-1064 §3.1 已批的 Latest Blog），不属五要素，置于五要素之后收尾，内容不删。

### 2.2 逐要素规格

#### ① Hero

| 元素 | 组件 | Token/Props | 行为 |
|---|---|---|---|
| 标语区 | 现状文本块保留 | 迁移现 hardcoded Tailwind 灰阶为 token（见 §4.2） | — |
| 搜索框 | `Antd Input.Search` | `size="large" allowClear`，背景 `--form-input-bg`、边框 `--form-input-border`、聚焦 `--form-input-focus-border` | 回车/点击 → `/search?q=`（NFM-1064 §3.1 同款）；`< md` 显示独立「搜索」按钮（§4.3，不依赖回车） |
| 快捷 chips | `<Link>` 药丸 | 边框 `var(--color-border)`、hover 边框/文字 `var(--color-accent)` | 点击 → `/potentials?type=eam` / `/potentials?element=U-Mo` 等；6 个：4 势类型 + 2 高频体系 |

#### ② 统计数据区

| 元素 | 组件 | 数据字段 | API（NFM-1064 附录 A） |
|---|---|---|--- |
| 统计卡 ×4 | **新增 `<StatCard>`**（附录 C 已立项，低复杂度：数值+标签+图标） | `label`, `value` | `GET /api/v1/potentials` → total（势函数总数）；`GET /api/v1/materials` → total（材料体系数）；`GET /api/v1/literature` → total（文献条目）；`GET /api/v1/properties` → total（属性记录） |

卡片样式：背景 `var(--color-surface)`、边框 `var(--color-border)`、数值 `font-variant-numeric: tabular-nums`（§6.2 数字对齐）、`text-3xl` 级字号。加载态用骨架占位（§5.1）。platform-design 另列「benchmark 条目数、下载量」——benchmark 中心未实现（09-04 差距 #5）、下载量无埋点（差距 #8），**本期不做**，待后端就绪后在 4 列网格扩为 6 列（`grid-cols-2 md:grid-cols-3 lg:grid-cols-6` 预留）。

#### ③ 热门势函数区

| 元素 | 组件 | 规格 |
|---|---|---|
| 卡片 ×8 | **复用 `<PotentialCard>`**（附录 B） | 类型色彩标签按 §2.1 势函数色彩编码；点击 → `/potentials/{id}` |
| 区标题 + 更多 | `<Link>` | 「热门势函数」+「查看全部 →」→ `/potentials` |
| 数据 | `GET /api/v1/potentials?sort=<热度>&limit=8` | 字段：`id, name, type, elements, quality/summary` |

**后端依赖（明示）：** 现无浏览/下载热度信号。过渡排序 = `updated_at` 降序（数据已存在），区标题过渡期文案「最新势函数」；热度端点（views/downloads 聚合）就绪后切排序参数与文案，组件零改动。

#### ④ 最新更新区

| 元素 | 组件 | 规格 |
|---|---|---|
| 更新列表 | `Antd List` `size="small"` + `split` | 每行：实体类型 `Antd Tag`（势函数=蓝/材料=绿）+ 名称链接 + 变更摘要 + 相对日期（`--color-text-secondary`） |
| 数据 | 过渡：`GET /api/v1/potentials?sort=updated_at&limit=6` | 目标：活动流端点（发布/版本更新/benchmark 补充，platform-design 定义）——**后端依赖，落地前以势函数 updated_at 单源呈现** |
| 空态 | NFM-1064 §5.2 | 「暂无更新」+ 引导「浏览全部势函数」 |

#### ⑤ 体系导航区

分组依据 platform-design 材料体系子节点，四组互斥且覆盖现有体系：

| 分组 | 成员 → `/materials/{slug}` |
|---|---|
| 金属燃料 | U · U-Mo · U-Zr |
| 氧化物燃料 | UO₂ |
| 包壳 | Zr · Zr-Nb · Fe-Cr-Ni |
| 裂变气体 | Xe/Kr/He in matrix |

| 元素 | 组件 | 规格 |
|---|---|---|
| 分组卡 ×4 | `Antd Card` `size="small"` + 组内 `<Link>` chips（组合现有组件，**不新增自定义组件**） | 卡标题 = 分组名（`--form-label-color`）；chip hover 边框/文字 `var(--color-accent)`；分子式用 `<sub>`（UO<sub>2</sub>，§6.2） |
| 布局 | `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4` | 组内 chip 自动换行 |

### 2.3 响应式与状态（引用基线，不重述）

- 断点行为全量遵循 NFM-1064 §4.1/§4.2；首页新增区的降级：②统计卡 4→2→1 列（`grid-cols-2` @`sm` 起）、③热门卡片 4→2→1 列、⑤体系卡 4→2→1 列。
- 加载/空/错误态一律引用 §5.1–§5.3（统计区骨架屏、列表区 `Antd Skeleton`、错误 `Antd Result status="error"` + 重试）。
- 首屏性能：①② 服务端取数优先（RSC），③④⑤⑥ 可流式/客户端追加，保证 LCP 元素（Hero）不被下方区块阻塞。

---

## 3. 路由映射与重定向表（CPO 裁决成文）

| 现状 | 目标 | 说明 |
|---|---|---|
| `/browse` | `/potentials` | 列表页（材料+势函数） |
| `/potential/[id]` | `/potentials/[id]` | 详情 |
| `/compare` | `/potentials/compare` | 对比，挂势函数库下 |
| `/search` | `/search`（不变） | 入口降为势函数库二级 |
| `/materials` `/literature` `/blog` `/about` | 不变 | — |

**重定向实现要求：**

1. 全部旧路径配 **永久重定向**（Next.js `redirects()` 返回 `permanent: true`，HTTP 308）：`/browse`→`/potentials`、`/potential/:id`→`/potentials/:id`（含通配）、`/compare`→`/potentials/compare`。查询串原样透传（`/browse?type=EAM` → `/potentials?type=EAM`）。
2. 站内所有 `<Link>`、面包屑、Dropdown/Drawer 菜单、页脚直接指向目标路径，**不得**依赖重定向兜底（重定向只服务外部旧链/收藏）。
3. 面包屑同步改写：`首页 / 浏览 / UO₂` → `首页 / 势函数库 / {name}`（NFM-1064 §3.4 面包屑规格不变，仅层级名替换）。
4. platform-design 站点地图写 `/potentials/{slug}`；本期沿用 `[id]`（裁决表为准），slug 化留待后续数据层议题，不在本 delta 强制。

---

## 4. Token / 组件合规声明

### 4.1 硬规则（全量继承 NFM-1064 §2）

- 所有新 UI 颜色/间距/字体/圆角/阴影 **仅用现有 CSS custom properties**（§2.1 清单）与 Tailwind spacing scale；**零新增 token、零硬编码像素**。
- 组件优先级 **Antd5 > Tailwind4 > ECharts**（ECharts 用 `nfmDarkTheme`）；**禁新增任何 NPM UI 库**。
- 新增自定义组件仅 1 个：`<StatCard>`（附录 C 已立项）。其余全部复用/组合现有组件（附录 B + Antd 原语）。

### 4.2 现状违规迁移清单（本次重构必须一并清偿）

| 位置（origin/main） | 违规 | 迁移 |
|---|---|---|
| `apps/web/src/app/page.tsx` | `text-gray-300/400`、`bg-gray-700`、`border-gray-600`、`bg-blue-600` 等 Tailwind 裸色板类，绕开 token 体系 | 分别替换为 `var(--color-text-secondary)`、`var(--color-surface)` 变体、`var(--color-border)`、`var(--color-accent)` 系 |
| `apps/web/src/components/blog/SiteHeader.tsx` | inline style 硬编码 `0.75rem 1.5rem` / `1.125rem` / `0.9375rem` / `gap:1.5rem` | 改 Tailwind spacing（`px-6 py-3` / `text-lg` / `text-[0.9375rem]` 归一到 `text-base` / `gap-6`）或 §2.1 token |

### 4.3 无障碍合规（继承 §6）

键盘可达性按 1.3/1.5 规格；触控区 ≥44px（§4.3）；`prefers-reduced-motion` 下关闭 Drawer 滑动动画；导航语义 `<header><nav aria-label>`；中文行高 1.75 / 混排 1.6（§6.2）。

---

## 5. 验收清单（对齐 NFM-4985 AC）

- [x] ① 导航定稿：一级恰 5 项、无「浏览/高级检索/对比」；二级形态有触发/键盘/移动端完整规格（§1.1–§1.5）
- [x] ② 首页五要素：布局图 + 组件映射 + 数据字段/API + 后端依赖明示（§2）
- [x] ③ 路由映射与重定向表成文（§3）
- [x] ④ Token/组件合规声明 + 现状违规迁移清单（§4）

*文档结束 — 交 CPO 签收（parent NFM-4984），签收后由 Lead Engineer 依 §3→§1→§2 顺序实现。*
