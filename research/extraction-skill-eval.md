# 材料提取技能实测:nuclear-materials-skills-v4 对三基线文献的召回(#1264)

> *实地调研报告(AFK 研究代理产出,只记事实与实测,不做产品决策)。*
> 分支:`research/extraction-skill-eval`(基于 `origin/main` @ `edb0bd53b`)
> 日期:2026-09-08 · 实测机:Mac Studio(本机 Ollama)

## TL;DR

对 github.com/Etoile04/nuclear-materials-skills-v4 的 `nuclear-property-extraction-v4` 技能,用与既有基线**同一本地模型**(`qwen3.8:27b-mlx`)在三篇基线文献摘要上各跑一次真实提取:

| 文献 | Ground Truth | 本体驱动管线(nucpot 端到端) | LLM 裸基线 | **技能 v4(本次实测)** |
|---|---|---|---|---|
| Beeler 2018(TDE 数值×4) | 4 | 1/4 = 25% | 4/4 = 100% | **4/4 = 100%** |
| Calhoun 2018(中子衍射事实×5) | 5 | 0/5 = 0% | 5/5 = 100% | **0/5 = 0%** |
| Zhu 2024(CALPHAD+ML 事实×5) | 5 | 0/5 = 0% | 5/5 = 100% | **0/5 = 0%** |
| 合计 | 14 | 1/14 ≈ 7% | 14/14 = 100% | **4/14 ≈ 29%** |

结论要点(事实层面):

1. **量化数值类事实(能落 property_measurements 的那种):技能 = LLM 裸基线 = 100%,远超本体管线的 25%。** 决定性机制是技能自带「`其他性能` + `property_note: "目录外"`」逃生舱——TDE 不在技能自己的 9 类核心目录里,但逃生舱让 4 个值全部通过;而 nucpot 本体 v0.4.0 的 11 类/74 属性目录没有等价逃生舱,静默丢了 3 条(§7.5.5-C 根因)。
2. **定性/机制/枚举类事实(Calhoun 的 slip 热激活、Zhu 的 5 个 MLIP 名):技能 = 0%,与本体管线同档,远低于 LLM 裸基线。** 这不是模型能力问题——模型推理文本里明确复述了这些事实——而是技能输出契约的结构性边界:四问判断 Q3(必须有单位/可量化)+ §12「不抽取无具体数字的定性描述」把它们挡在门外。**按技能自身设计意图,这类事实本来就不属于它的输出域。**
3. **工程接入面:主干差一个 adapter;真正的语义重构点只有一处半**——`conditions` 开放 JSONB ↔ 平台 `measurement_conditions` 4 个固定列的结构错配,以及 `phase`/`element`/`confidence` 三字段无落点(半个:可降级 notes,但 phase 对核数据是负载字段)。详见 §4。

对 #1252 的含义(数据,不是建议):「技能为抽取引擎」在**数值属性抽取**这个域内已达到 LLM 裸基线水平且自带溯源/条件/置信度结构;定性科学事实的召回缺口与本体管线同源(输出域约束),平台对该类事实的既有答案仍是 RAG 路线(技术总结报告 §7.6.3)。

---

## 1. 技能盘点(clone 于 /tmp/nuclear-materials-skills-v4,浅克隆 @ main)

### 1.1 仓库结构

```
nuclear-materials-skills-v4/
├── README.md                        # 总览:4 技能 + 流水线图 + Supabase 建表 SQL
├── nuclear-pipeline-v4/
│   └── SKILL.md                     # 编排层:PDF→MD→JSON→Supabase 端到端,3 个 CHECKPOINT
├── nuclear-property-extraction-v4/  # ★ 本次实测对象
│   ├── SKILL.md                     # 核心抽取规则(356 行)
│   └── references/
│       ├── phase_rules.md           # 物相归属三步法 + 标准 phase 映射表
│       ├── property_catalog.md      # 可抽取性四问 + 11 类固定枚举 + 标准属性/单位表
│       └── extraction_rules.md      # 全文扫描/数值格式/conditions/reference/输出格式
├── nuclear-query-v4/                # SKILL.md + query_v4.py + config.md(Supabase 查询)
└── supabase-importer-v4/            # SKILL.md + import_to_supabase_v4.py + config.md(JSON 入库)
```

### 1.2 输入形态

- **正典输入:文献 Markdown 文件**(流水线上游 `pdf_to_md_v2.py` 把 PDF 转 MD,放 `md_output/`)。一个 md 文件可含多篇论文,以 `REFERENCE:` 行分界。
- **PDF→MD 脚本不在仓库内**,流水线 SKILL.md 里硬编码了作者机器的绝对路径(`/home/zuozhuo/info-extract/.../pdf_to_md_v2.py`),可经 `PDF_TO_MD_SCRIPT` 环境变量覆盖。本次实测输入为摘要级 Markdown(与既有 LLM 裸基线输入一致,见 §2.1 公平性说明)。

### 1.3 输出契约(13 字段 + 1 可选)

```text
source_file → material_name → composition → phase → element → property_category
→ property → value → unit → conditions → context → confidence → reference [+ property_note]
```

| 字段 | 形态 | 说明 |
|---|---|---|
| `source_file` | string | md 源文件相对路径——**溯源粒度是文件级,无页码/行号/quote 定位** |
| `material_name` / `composition` | string/null | 禁止用外部常识补全成分 |
| `phase` | string/null | 按 phase_rules.md 标准化(`alpha`/`gamma`/`oxide`/`delta-hydride`/`SPP`…) |
| `element` | string/null | 性能直接相关元素(oxygen/hydrogen…) |
| `property_category` | 11 值固定枚举 | 9 核心类(密度/比热容/热传导率/弹塑性模型/热膨胀/辐照蠕变/辐照肿胀/腐蚀/硬化性能)+ 2 支持类(材料规格组织信息/其他性能) |
| `property` | string | 优先 property_catalog.md 标准名(中文),表外可抽但须标 `property_note:"目录外"` |
| `value` | **string** | 保留原文精度/范围/约数/科学计数法:`"73.2"`、`"3 to 4"`、`"200 ± 10"` |
| `unit` | string/null | 原文单位不换算,仅做字形标准化(`µm→μm`) |
| `conditions` | JSONB | 开放键集:`condition_type`(experimental/simulation/service/processing/mixed/unknown)+ temp_C/temp_K/stress_MPa/dpa/fluence/flux/burnup/simulation_method/model_name/processing_state… |
| `context` | string/null | 补充说明 |
| `confidence` | **三档枚举** high/medium/low | 按「字段齐全度」规则判定,**无数值置信度**(与平台 0.72–0.97 数值置信度不同粒度) |
| `reference` | string | `作者, 文章标题`(去期刊/年份/DOI) |

### 1.4 模型与"本体"依赖

- **不绑定任何特定模型/推理后端。** SKILL.md 是 Agent Skills 标准格式,声明兼容 Claude Code / OpenClaw / Codex / Cursor / OpenCode / Gemini CLI 等——**执行者就是宿主 Agent 的 LLM**,技能本体是一份规则集(prompt-as-skill),没有可独立运行的抽取 CLI。
- 明文禁止把抽取降级为正则/关键词/表格模板脚本("脚本只能搬运文件,不能理解论文");脚本件仅 query/importer 两个(Supabase 侧)。
- **"本体"是轻量受控词表,不是形式化本体**:property_catalog.md(11 类枚举+标准属性名+单位)+ phase_rules.md(标准 phase 映射)。与 nucpot 本体 v0.4.0(11 类/74 属性)同量级,但多了「目录外」逃生舱。

### 1.5 调用方式

- 设计用法:把技能目录装进 Agent 的 skills 路径,自然语言触发("抽取 md_output/xxx.md 的核材料性能数据"),Agent 读 SKILL.md+三份 references 后逐篇语义抽取,产出 `extraction_results/project_v4/<同名>.json`。
- **本次实测的等价复现**(见 §2.2):把 SKILL.md + 三份 references 原文整体作为 system prompt,md 文件作为 user 消息,喂给本机 Ollama `qwen3.8:27b-mlx`——即"一个只有 LLM 的最小宿主 Agent"。这正是技能声明兼容的调用形态之一。

## 2. 基线对照实测

### 2.1 输入与 ground truth

三篇基线文献摘要:Beeler 2018 与 Calhoun 2018 从文献库 API(`GET /api/v1/literature/{id}`,lit_id `6aab9f24…`/`8e60e867…`)的 `content_md` 中截取 Abstract 段;Zhu 2024 直接取 API `abstract` 字段(lit_id `cd36999a…`)。ground truth 取技术总结报告 §7.6.1(与 issue #1264 一致:4/5/5):

- **Beeler 2018(4)**:γ-U@800K TDE = 73.2 eV(U MEAM)/ 47.1 eV(U-Zr MEAM)/ 35.6 eV(U-Mo ADP);α-U@600K TDE = 66.3 eV(U-Mo ADP)
- **Calhoun 2018(5)**:①温度区间 25–150°C ②slip 系热激活响应 ③twinning athermal ④热残余应力强烈影响晶族内应变演化 ⑤残余应力对宏观流动曲线几乎无影响(除弹-塑性转变区)
- **Zhu 2024(5)**:①5 个 MLIP 名(M3GNet/CHGNet/MACE/SevenNet/ORB) ②3 个案例(Cr-Mo/Cu-Au/Pt-W) ③ORB 加速 >3 数量级(vs DFT) ④保持相稳定性精度 ⑤扩展到 Cr-Mo-V 三元

公平性:输入=摘要纯文本、模型=与 §7.5.3 LLM 裸基线完全相同的 `qwen3.8:27b-mlx`,与既有两条基线同输入同模型,唯一新增变量=技能规则集(SKILL.md+三 references ≈ 11.6k prompt tokens)。

### 2.2 实测命令与设置

```bash
git clone --depth 1 https://github.com/Etoile04/nuclear-materials-skills-v4 /tmp/nuclear-materials-skills-v4
# 三篇摘要写成技能输入格式的 md(title + REFERENCE: 行 + Abstract),放 /tmp/skill-eval/md_output/
python3 /tmp/skill-eval/run_skill.py qwen3.8:27b-mlx \
    md_output/Beeler_2018_TDE_U.md md_output/Beeler_2018_TDE_U.md out/Beeler_2018_TDE_U.json
```

`run_skill.py` 要点:system = SKILL.md + phase_rules.md + property_catalog.md + extraction_rules.md 原文拼接(技能第 2 节规定执行前必读这四份);user = md 文件 + 按规则抽取并只输出 JSON 数组的指令;`options: {num_ctx: 32768, temperature: 0}`,`think: false`。每次调用一个独立进程(无跨篇 KV 复用)。**共 3 次 LLM 调用,零重试**(三份输出均含可解析的合法 JSON,未触发技能 fallback 表的重试条款)。

偏差声明(wayfinder 纪律):技能 CHECKPOINT 要求首篇样板经用户确认后批量——AFK 环境无确认人,三篇均单发照跑并在本报告完整披露原始输出;其余规则(必读文件、输出路径、字段顺序、JSON 校验)全部照办。

### 2.3 原始输出摘要(全文见附录 A;完整原文在分支同名目录未入仓,以附录为准)

**Beeler 2018 — 4 条记录,112.4s(prompt 11,788 tok / gen 3,509 tok)。** 模型先在推理文本中完成四问判断与归类论证(TDE 不属 9 类核心 → `其他性能`+`property_note:"目录外"`;γ→`gamma`、α→`alpha`;MD→`conditions.simulation_method`,势函数→`conditions.model_name`,温度→`temp_K`),然后输出 4 条记录,值/单位/温度/势函数/物相与 ground truth 一一对应:

```json
{"source_file":"md_output/Beeler_2018_TDE_U.md","material_name":"U","composition":"U",
 "phase":"gamma","element":null,"property_category":"其他性能","property":"位移阈值能",
 "value":"73.2","unit":"eV",
 "conditions":{"condition_type":"simulation","temp_K":800,"simulation_method":"MD","model_name":"U MEAM"},
 "context":"γ相铀在800K下使用U MEAM势的位移阈值能","confidence":"high",
 "reference":"Beeler, B., Zhang, Y., Okuniewski, M., Deo, C., Calculation of the displacement energy of α and γ uranium",
 "property_note":"目录外"}
```

(47.1 eV/U-Zr MEAM/800K/gamma、35.6 eV/U-Mo ADP/800K/gamma、66.3 eV/U-Mo ADP/600K/alpha 三条同构,略。)

**Calhoun 2018 — 0 条记录(空数组),32.8s(prompt 11,586 / gen 713)。** 模型推理原文明确认出了事实②③④⑤(slip 热激活、twinning athermal、残余应力对内应变/宏观流的相反影响)与温度区间 25–150°C,但按规则判定:摘要"没有可量化的性能数值"、25–150°C"属于条件而非性能数据"、定性结论按 §12 不抽取 → 输出 `[]`。

**Zhu 2024 — 0 条记录(空数组),140.9s(prompt 11,707 / gen 648)。** 同样:模型认出 MLIP 名单、案例、">3 数量级加速",但判定"three orders of magnitude 是定性/半定量描述""MLIP 名不是材料性能""方法论文章无可抽取数据点" → 输出 `[]`。

### 2.4 召回计算与三线对照

| 文献 | GT 事实 | 技能命中 | 明细 | 技能召回 | 本体管线 | LLM 裸基线 |
|---|---|---|---|---|---|---|
| Beeler 2018 | 4 | 4 | 73.2 ✓ 47.1 ✓ 35.6 ✓ 66.3 ✓(值+势函数+相+温度全对) | **100%** | 25% | 100% |
| Calhoun 2018 | 5 | 0 | 契约拒收:②–⑤为定性机制事实,Q3 不过;①温度区间被判为 condition 不独立成记录 | **0%** | 0% | 100% |
| Zhu 2024 | 5 | 0 | 契约拒收:MLIP 名/案例/加速比为枚举与半定量事实,非属性-数值记录 | **0%** | 0% | 100% |
| **合计** | **14** | **4** | | **28.6%** | 7.1% | 100% |

判定口径:与 §7.5.3/§7.6.2 一致——命中 = 输出中能对应 ground truth 事实(数值类对值,事实类对语义)。

**归因(证据可查)**:

- Beeler 100% 的机制 = **逃生舱**。技能目录同样没有 TDE(9 核心类不含它),但 `其他性能+目录外` 让 4 条全过;nucpot 管线无逃生舱,schema 过滤掉 3 条(§7.5.5-C)。「prompt 本体化 ≠ 抽取质量达标」的差距,被一个目录外豁免字段抹平了。
- Calhoun/Zhu 0% 的机制 = **输出域边界,非能力缺口**。两次 run 的推理文本都完整复述了 ground truth 事实(模型读懂了),是契约(属性-数值-单位记录 + §12 黑名单)主动拒收。LLM 裸基线 100% 恰恰因为它允许输出无单位的机制性事实——两者的 ground truth 口径相同,但输出域不同。
- 耗时:32.8–140.9s/篇(27b,冷 prompt 每篇 ~11.6k tok 规则+摘要),与 LLM 裸基线 Beeler 冷跑 103.5s 同量级;规则集的 token 开销可接受。

### 2.5 可复现性附注

`ollama list` 中仅有 `qwen3.8:27b-mlx`(18GB MLX)一个 ≥4b 生成模型(另有 qwen3.5:4b-nvfp4);本次全部用 27b,与既有基线主表同模型。温度 0、think 关闭、num_ctx 32768。原始三份输出+meta 已存 `/tmp/skill-eval/out/`(本机),关键内容收录于附录 A。

## 3. 工程接入面:技能输出 ↔ 平台数据后端

平台侧读的是 `apps/api/src/nfm_db/models/property.py` / `source.py` 现行 ORM(migration 已到 079+)。逐字段映射距离:

| 技能字段 | 平台落点 | 距离 |
|---|---|---|
| `material_name`/`composition` | `materials`(规范化表,dataset 经 FK 引用) | **adapter**:名称解析/建档(lookup-or-create),成分字符串解析 |
| `property_category`(11 中文枚举) | `property_categories`(name/slug) | **有界 crosswalk**:11 行对照表 |
| `property`(中文标准名/目录外) | `property_types`(name/slug/value_type,default_unit) | **crosswalk + 建档流**:74 属性之外的(如位移阈值能)走新建 PropertyType;`property_note:"目录外"` 天然映射到"新类型待审" |
| `value` `"73.2"`/`"3 to 4"`/`"200 ± 10"`/`"~200"` | `value_scalar`/`value_min`/`value_max`/`uncertainty`/`value_text`(NUMERIC(20,15) 多型值,有 CHECK 至少一值) | **adapter+解析规则**:单值→scalar,范围→min/max,±→scalar+uncertainty,`~`/精度保持串→value_text 兜底(数值列会吃掉尾零语义) |
| `unit` `"eV"`/`"μm"` | `units` 表 FK | **adapter**:单位字符串→规范化单位行(查找或建) |
| `conditions`(开放 JSONB:condition_type/temp_K/stress_MPa/dpa/simulation_method/model_name/processing_state/…) | `measurement_conditions`(**仅** temperature/pressure/environment/irradiation_dose/notes 4.5 列) | **★语义重构点**:simulation_method/model_name/processing_state/stress/strain_rate/fluence/flux/burnup 全部无列——要么扩展 schema(加列或 JSONB),要么降级塞 notes(丢结构、丢检索)。且平台 temperature 单列无单位标记,adapter 必须保 `temp_K`/`temp_C` 原单位语义,否则复刻 §7.5.5-D 的"800K→526.85°C 失联"根因 |
| `phase` `gamma`/`oxide`/`delta-hydride` | **无对应列** | **半个重构点**:核数据的负载字段,塞 notes 可运行但不可查询;建议加列 |
| `element` | 无对应列 | 降级 notes(可接受) |
| `confidence` high/medium/low | 无对应列;行级有 `review_status` 默认 `pending` | 三档→审校排序/notes;注意与平台既有数值置信度(0.72–0.97)粒度不同 |
| `context` | `notes` | adapter |
| `reference` `作者, 标题` 串 | `data_sources`(结构化 doi/title/journal/year/authors 规范化)+`datasets.source_id` | **adapter+解析**:字符串拆作者/标题,或按标题/DOI 回查 literature API 建档(更稳) |
| `source_file` md 路径 | `data_sources.file_path`(另有 file_hash/content_md) | adapter:路径重映射 |
| —(去重) | `uq_pm_dedup`(dataset+property_type+conditions_hash+method 5 元组) | adapter 侧需生成 conditions_hash 与 method(可从 condition_type/simulation_method 导出) |
| —(人工校对队列) | `review_status`/`reviewer_note`/`reviewed_at` 现成 | 契约契合:所有技能记录直接以 pending 入队 |

**判断:差一个 adapter,外加一处半语义重构。** 13 个技能字段中 9 个可经解析/对照表直接落位;真正要动平台 schema 的只有 `measurement_conditions`(开放 conditions 无处安放——这是唯一硬阻塞)和可选的 `phase` 列。不是全面语义重构;但若不扩 conditions schema 而选择塞 notes,技能输出里最有价值的结构化条件(simulation_method/model_name/temp 单位语义)会在入库时丢失,等于只买了"值+溯源"、丢掉"条件"。

另两点工程事实:① 技能原始输出是 Agent 风格文本(推理前言+JSON+写文件代码块),**不是纯 JSON**,headless 接入需要一层 JSON 抽取/结构化输出包装(实测用正则取首个 ```json 块即可);② 溯源粒度止于文件级(source_file),无页/段/quote 定位——平台若要证据级溯源,该信息在技能输出里本来就不存在。

## 4. 附录 A:原始输出(模型生成文本,仅裁剪重复块)

### A.1 Beeler 2018(out/Beeler_2018_TDE_U.json,112.4s)

推理段(节选):

> 根据文献内容分析:1. γ U 在 800 K 的 TDE(U MEAM): 73.2 eV … TDE 不属于9个核心性能类别中的任何一个 … 根据四问判断:有明确材料对象(α-U, γ-U),有数值和单位(eV),来自MD模拟结果 … 归入 `其他性能`,添加 `property_note: "目录外"`。γ U → phase = `gamma` …

JSON:4 条记录(§2.3 已全文列第 1 条,其余 3 条同构:47.1/U-Zr MEAM/800K/gamma、35.6/U-Mo ADP/800K/gamma、66.3/U-Mo ADP/600K/alpha),`confidence:"high"` 全部,`conditions.condition_type:"simulation"`、`simulation_method:"MD"`。

### A.2 Calhoun 2018(out/Calhoun_2018_neutron_diffraction_U.json,32.8s)

> Q1:摘要中没有给出具体的性能数值 … 提到的"25 to 150°C"是实验温度范围,属于条件而非性能数据。Q3:无单位化的性能数值。结论:该文献摘要中不包含可抽取的核材料性能数据。… 根据规则第12节"不抽取"条款,无具体数字的趋势或定性描述不应抽取。因此,输出为空 JSON 数组。`[]`

### A.3 Zhu 2024(out/Zhu_2024_MLIP_CALPHAD.json,140.9s)

> "computational speedups exceeding three orders of magnitude",这是一个定性/半定量的描述 … 文章讨论的是计算方法 … 没有报告具体的材料性能数值 … 这是一篇纯方法论/观点文章 … 输出空 JSON 数组。`[]`

## 5. 交叉引用

- issue:#1264(本票)、#1252(召回路线决策)、#1249(地图)
- 数据:技术总结报告 §7.5.3/§7.6(基线与 ground truth);文献库 API lit_id `6aab9f24-ec20-4e89-8a89-70e9c44b8dba` / `8e60e867-a139-47a8-ae18-470be5eff911` / `cd36999a-b0a0-4d76-935f-6595bb09cb64`
- 平台 schema:`apps/api/src/nfm_db/models/property.py`(PropertyMeasurement/MeasurementCondition/Dataset/PropertyType)、`apps/api/src/nfm_db/models/source.py`(DataSource)
- 技能源:/tmp/nuclear-materials-skills-v4 @ main(浅克隆,留在 /tmp 未入仓)
