# 势函数平台质量评估与自动化验算调研报告

> **调研日期**：2026-05-26
> **调研目的**：为 NucPot 核材料势函数库平台设计质量评估与自动化验算模块提供技术参考
> **调研范围**：OpenKIM、NIST IPR、ML 势验证生态、自动化验算技术栈

---

## 1. 摘要

本报告系统调研了主流势函数平台对收录势函数的质量评估与自动化验算机制。OpenKIM 采用了当前最完善的评估框架——基于 Crystal Genome (XtalG) 技术的属性预测管线与 Verification Check (VC) 代码完整性检查体系，配合 KIM API 实现跨模拟器的即插即用测试。NIST IPR 则通过 iprPy 高通量计算框架，对收录势函数执行 22 种标准化计算（晶格常数、弹性常数、表面能、层错能、点缺陷等），结果以人机可读格式公开。机器学习势函数领域发展出独特的验证范式：基于 DFT 参考数据的逐点误差分析、主动学习的不确定性量化、以及标准 benchmark 数据集的统一评估。综合以上调研，本报告提出了适合核材料领域的三级验证策略（基础/标准/全面），以及基于 LAMMPS + ASE 的轻量级自动化验算技术路线。

---

## 2. 引言

原子间势函数（interatomic potential）是分子动力学和多尺度模拟的核心输入。势函数的质量直接影响模拟结果的可靠性和可重复性。然而，势函数的开发、分发和使用长期面临几个关键挑战：不同模拟器之间的接口不统一导致同一势函数在不同软件中表现不一致；势函数的编码正确性缺乏系统验证；预测精度缺少标准化的评估方法；实验或 DFT 参考数据分散，难以系统对比。

针对这些问题，国际上已发展出多个势函数库和评估平台。其中最具代表性的是美国 NSF 资助的 OpenKIM 项目和 NIST 维护的 Interatomic Potentials Repository (IPR)。近年来，随着机器学习势函数的兴起，验证和 benchmark 方法也在快速演进。本报告深入调研了这些平台的评估框架和技术实现，为 NucPot 平台的质量评估模块设计提供参考。

---

## 3. OpenKIM 评估体系详析

OpenKIM（Open Knowledgebase of Interatomic Models）成立于 2009 年，由美国国家科学基金会（NSF）资助，明尼苏达大学 Ellad B. Tadmor 教授领导的团队开发和维护。OpenKIM 是当前最系统化的势函数评估平台，其评估框架包含两个核心组件：KIM Tests（属性预测测试）和 Verification Checks（代码完整性验证）。

### 3.1 KIM Tests 与 Test Driver 架构

KIM Tests 的核心思想是将势函数的属性预测过程标准化、自动化。每个 KIM Test 对应一个或多个材料属性的计算（如晶格常数、弹性常数、表面能等），该计算过程严格遵循 KIM Property Definition 定义的规范格式。

Test Driver 是 KIM Tests 的关键架构创新。一个 Test Driver 实现了一类属性的计算方法，可以自动应用于所有兼容的势函数模型。例如，一个计算 fcc 晶体表面能的 Test Driver，可以通过参数文件指定不同的元素和晶面，自动生成多个 Tests。这种设计使得评估方法的扩展成本极低——新增一种材料体系只需一个参数文件，而非重新编写计算代码。

Test Driver 的实现方式有三种：

第一，基于 KIM API 的独立程序，用 C/C++/Fortran 编写，可以直接调用 Portable Model 的共享库进行计算。这种方式性能最高，但开发门槛也最高。

第二，基于模拟器脚本的实现，例如生成 LAMMPS 输入文件并执行。这种方式可以利用模拟器的成熟功能，但仅适用于特定的模拟器。

第三，基于 Atomic Simulation Environment (ASE) 的 Python 脚本。这是 OpenKIM 推荐的方式，因为它同时支持 Portable Model 和 Simulator Model，并且可以适配 ASE 支持的所有模拟器后端。

### 3.2 Crystal Genome (XtalG) 框架

OpenKIM 当前正在实施一项重大升级——Crystal Genome (XtalG) 项目。XtalG 将 KIM Test Driver 泛化到所有已知晶体结构，实现了对势函数属性的系统性计算。

XtalG 的工作流程如下：首先，基于 AFLOW 原型标签系统对已知晶体结构进行分类；然后，对每种晶体结构，自动生成势函数的属性预测计算任务；计算过程中使用统一的 Python 后端进行前处理和后处理，确保正确性和一致性——特别是对张量属性（如弹性常数）的取向与晶体结构保持一致。

XtalG 计算涵盖的属性包括：平衡晶体结构、弹性常数、压力-体积曲线、基态晶体结构、空位形成能等。这些计算通过 kimvv（KIM Validation and Verification）Python 包提供给用户，允许用户在本地运行与 OpenKIM 云端完全相同的验证协议。

kimvv 包的使用方式简洁直观。用户只需提供势函数名称（KIM Model 字符串）或 ASE Calculator 对象，即可调用各种 Test Driver：

```python
from kimvv import ElasticConstantsCrystal
elast = ElasticConstantsCrystal('LennardJones_Ar')
from ase.build import bulk
atoms = bulk("Ar", "fcc", 5.0)
results = elast(atoms, method="stress-condensed")
```

每个 Test Driver 返回符合 KIM Properties Framework 的标准化字典，包含属性名、计算值、单位等完整元数据。

### 3.3 Verification Checks (VC) 体系

KIM Verification Checks 与 KIM Tests 形成互补：Tests 评估的是势函数的物理预测精度，而 VC 评估的是势函数实现的编码正确性和特性。

VC 分为三类：

**信息性 VC（Informational）** 提供势函数的固有特性信息，不影响使用决策，例如势函数的平滑度（连续性）程度。这类信息虽然不直接反映编码错误，但可以帮助用户理解势函数的行为特征，例如在能量最小化过程中可能出现的收敛困难。

**一致性 VC（Consistency）** 检查势函数的内部一致性。最重要的例子是力的一致性检查——将势函数返回的力与其能量的负梯度进行数值比较，如果偏差超过阈值则表明力的计算可能存在编码错误。

**强制性 VC（Mandatory）** 检查势函数是否满足关键要求。例如，势函数是否真正支持其声明的元素种类、单位转换是否正确、是否存在内存泄漏、是否线程安全。

每个 VC 返回一个等级评定。等级有两种模式：'graded' 模式返回 A/B/C/D/F 字母等级（A 为最优，F 为失败），'passfail' 模式返回 P（通过）或 F（失败）。每个 VC 生成一个符合标准 VC Property Definition 的结果文件（results.edn）和一份详细的人类可读报告（report.txt）。

### 3.4 KIM Processing Pipeline

OpenKIM 的自动化验算通过 KIM Processing Pipeline 实现。当新的势函数模型或新的 Test 上传到系统时，Pipeline 自动确定需要执行的计算任务并排队执行。

Pipeline 的技术架构采用分布式虚拟机系统，包含 directors（调度器）和 workers（计算节点），运行专用的 Python 守护进程，通过异步消息传递框架和消息队列系统进行通信。Pipeline 自动管理 Tests 之间的依赖关系——例如，计算弹性常数之前需要先对晶体结构进行弛豫。

所有计算结果存储在 OpenKIM Repository 中，用户可以在模型页面上通过嵌入式可视化工具和文本形式的"property synopses"查看结果。

### 3.5 Reference Data 与对比机制

OpenKIM 存储实验数据和第一性原理（DFT）计算结果作为 Reference Data，这些数据同样遵循 KIM Property Definition 格式。KIM 工具可以将 KIM Test 的预测结果与对应的 Reference Data 进行自动对比，帮助用户评估势函数的预测精度并选择适合其应用的模型。

### 3.6 DOI 与可引用性

OpenKIM 是 DataCite 的成员，为所有势函数模型、Tests、Test Drivers、Verification Checks 等内容分配 DOI（数字对象标识符）。这意味着用户可以在论文中引用特定版本的势函数，确保研究的可重复性。KIM 内容也被 Clarivate 的 Web of Science Data Citation Index 索引。

---

## 4. NIST IPR 评估体系详析

NIST Interatomic Potentials Repository（IPR）位于 https://www.ctcms.nist.gov/potentials/，由美国国家标准与技术研究院（NIST）维护。NIST IPR 是 OpenKIM 的重要合作伙伴，两个平台在势函数评估领域形成了互补关系。

### 4.1 Curation 流程

NIST IPR 采用人工 curator 模式。研究人员将势函数文件通过电子邮件提交给 NIST 团队，由专业人员进行审核、格式标准化和收录。这种模式确保了收录势函数的质量，但扩展性有限——随着势函数数量增长，审核效率成为瓶颈。

收录的势函数文件按统一格式存储，包括 EAM 的 `setfl` 格式、MEAM 的参数文件、LAMMPS 的 `pair_style/pair_coeff` 命令格式等。NIST 还维护着势函数与文献的对应关系，每个势函数都标注了原始论文的引用信息。

### 4.2 iprPy 高通量计算框架

iprPy 是 NIST IPR 的核心验算工具，以开源 Python 包形式发布在 GitHub（https://github.com/usnistgov/iprPy）。iprPy 的设计目标是使势函数性质计算的使用门槛尽可能低，无论是对用户还是开发者。

iprPy 实现了 22 种标准化计算方法（Calculation Styles），涵盖：

**基础性质**：`E_vs_r_scan`（能量-距离曲线扫描）、`diatom_scan`（双原子扫描）、`bond_angle_scan`（键角扫描）、`isolated_atom`（孤立原子能量）、`energy_check`（能量检查）。

**晶体性质**：`crystal_space_group`（空间群分析）、`relax_box`（盒子弛豫）、`relax_static`（静态弛豫）、`relax_dynamic`（动力学弛豫）、`relax_liquid`（液态弛豫）、`free_energy`（自由能）、`free_energy_liquid`（液态自由能）。

**力学性质**：`elastic_constants_static`（静态弹性常数）。

**缺陷性质**：`point_defect_static`（点缺陷形成能）、`point_defect_diffusion`（点缺陷扩散）、`stacking_fault_static`（层错能）、`stacking_fault_map_2D`（二维层错能图）、`surface_energy_static`（表面能）、`dislocation_monopole`（位错单极）、`dislocation_periodic_array`（周期位错阵列）、`dislocation_SDVPN`（位错 SDVPN 方法）。

**振动性质**：`phonon`（声子谱计算）。

iprPy 的架构设计遵循以下原则：所有代码开源；计算文档和 Python 代码易于访问和审查；支持单独或批量执行；命令行接口使操作不依赖 Python 知识；模块化设计便于添加新方法；新计算可以复用现有计算的输入/输出术语；结果记录同时适合人类阅读和机器解析。

iprPy 的工作流程分为准备（prepare）和执行（run）两个阶段。准备阶段根据输入参数模板生成所有计算任务的配置文件，执行阶段通过 runner 进程批量提交计算任务。支持在本地工作站和集群（作为集群作业）上运行。

### 4.3 NIST IPR 与 OpenKIM 的关系

NIST IPR 和 OpenKIM 存在紧密的合作关系。NIST 是 OpenKIM 项目的合作伙伴机构之一。在实际操作中，两个平台的侧重点有所不同：NIST IPR 更注重势函数文件的存储和分发，通过 iprPy 提供可下载的计算工具；OpenKIM 更注重在线自动化评估，通过 Processing Pipeline 在云端执行计算并展示结果。

用户可以在 NIST IPR 下载势函数文件后，使用 iprPy 在本地执行验算，也可以将势函数上传到 OpenKIM 获取系统性的在线评估。两种方式互为补充。

---

## 5. 其他平台与 ML 势验证

### 5.1 传统势函数库

**POTLIB**（http://www.potlib.net/）是一个早期的势函数在线存储库，主要收录化学反应动力学领域的势能面。POTLIB 的评估方式较为简单，主要依赖开发者自测和社区反馈，缺乏系统化的自动化验算机制。

**Materials Project / Pymatgen 生态** 提供了丰富的材料性质计算和分析工具。虽然不直接专注于势函数评估，但 pymatgen 的 PhaseDiagram、StructureMatcher 等工具可以用于评估势函数预测的相稳定性、结构相似性等。Materials Project 的计算工作流（atomate）展示了高通量材料计算的最佳实践，包括自动化计算、错误处理、数据管理等，这些设计理念可以借鉴到势函数评估平台中。

### 5.2 机器学习势函数的验证方法

机器学习（ML）势函数的验证与传统势函数有本质区别。传统势函数的物理形式是人为设计的，验证重点是参数正确性和基本物理约束的满足；ML 势函数通过数据驱动学习得到，验证重点是拟合精度、泛化能力和物理一致性。

**MACE** 是当前最活跃的 ML 势函数框架之一，其验证工具链包括：训练集/验证集/测试集的误差分析（Energy MAE、Force MAE、Stress MAE）、主动学习循环（基于模型不确定性的训练数据扩展）、以及标准 benchmark 数据集上的性能评估。MACE 团队维护了多个 benchmark 数据集（如 MACE-MP-0 训练用的 Materials Project 子集），用于统一评估不同模型的预测能力。

**NequIP/Allegro** 是基于等变神经网络的 ML 势函数框架。其验证方法侧重于：旋转等变性测试（确保预测结果不依赖于坐标系选择）、对训练域外结构的泛化测试、以及与 DFT 参考数据的逐构型对比。NequIP 的 benchmark 通常报告在特定数据集（如 revMD17、3BPA 等）上的 Energy/Force MAE。

**FLARE**（Fast Learning of Atomistic Rare Events）采用主动学习策略进行势函数验证。核心思路是：在高不确定性区域自动触发 DFT 计算，将新数据加入训练集，反复迭代直到不确定性降到阈值以下。这种方式本质上是一种自适应验证——不是预先定义固定的验证指标，而是通过学习过程本身发现势函数的弱点。

**DeepMD/DP** 是中国在 ML 势函数领域的代表性工作。DeepMD-kit 的验证框架包括：模型压缩前后的精度对比、DP GEN（深度势能生成器）的迭代学习循环中的收敛监控、以及标准测试集上的 benchmark 报告。

### 5.3 标准 Benchmark 数据集

ML 势函数领域已发展出多个标准 benchmark 数据集，用于统一评估和对比不同模型的性能：

**revMD17**：包含 8 个小有机分子的 DFT 参考数据，广泛用于评估 ML 势函数对分子系统的 Energy 和 Force 预测精度。

**3BPA**：Nuttall 等人构建的双酚 A 衍生物数据集，用于测试 ML 势函数对未见过构型的泛化能力。

**Materials Project 子集**：用于大规模固态势函数训练和测试，MACE-MP-0 等模型在此基础上训练。

**OpenKIM Reference Data**：OpenKIM 平台积累的实验和 DFT 参考数据，以标准化格式存储，可直接与 Test 预测结果对比。

这些 benchmark 的核心价值在于提供了统一的比较基准——不同研究组开发的 ML 势函数可以在相同数据集上进行公平对比。

---

## 6. 自动化验算技术栈

### 6.1 架构设计参考

综合 OpenKIM Pipeline 和 iprPy 的架构经验，自动化验算系统应包含以下核心组件：

**任务调度器**：负责接收验算请求、确定依赖关系、分配计算资源。OpenKIM 采用基于消息队列的异步架构（directors + workers），iprPy 采用基于文件系统的批处理架构（prepare + run）。对于 NucPot 的初期实现，基于文件系统的批处理方案更为简洁实用。

**计算引擎**：执行实际的模拟计算。势函数验算的主流引擎是 LAMMPS（覆盖 EAM/MEAM/ML 势等大部分类型）和 GULP（覆盖 Buckingham 等对势）。通过 ASE（Atomic Simulation Environment）的 Calculator 接口可以实现引擎的统一封装——ASE 支持 LAMMPS、GULP、VASP、QE 等多种后端。

**结果采集与存储**：计算完成后提取关键属性值，存入数据库。建议采用 JSON 格式存储，与势函数元数据在同一数据结构中。

**对比分析**：将计算值与实验/DFT 参考数据进行对比，计算误差指标。

**可视化报告**：生成对比图表（类似 OpenKIM 的 property synopsis），以 Web 页面形式展示。

### 6.2 核心计算属性

势函数质量评估应覆盖以下核心属性（按优先级排列）：

**第一优先级（基础验证）**：
- 晶格常数（lattice constant）：衡量势函数对平衡晶体结构的预测
- 结合能/内聚能（cohesive energy）：衡量势函数对键强度的预测
- 弹性常数（elastic constants C11/C12/C44）：衡量势函数对力学响应的预测

**第二优先级（标准验证）**：
- 体模量（bulk modulus）
- 空位形成能（vacancy formation energy）
- 表面能（surface energy）
- 层错能（stacking fault energy）
- 熔点（melting point）：通过固-液共存法或 NPT 升温法确定

**第三优先级（全面验证）**：
- 自由能（free energy）：通过热力学积分计算
- 声子谱（phonon dispersion）
- 扩散系数（diffusion coefficient）
- 点缺陷迁移能（migration energy barrier）
- 位错核心结构（dislocation core structure）

### 6.3 误差度量标准

势函数预测值与参考值之间的误差评估采用以下度量：

**绝对误差（AE）**：$\text{AE} = |x_{\text{calc}} - x_{\text{ref}}|$

**相对误差（RE）**：$\text{RE} = \frac{|x_{\text{calc}} - x_{\text{ref}}|}{|x_{\text{ref}}|} \times 100\%$

**平均绝对误差（MAE）**：$\text{MAE} = \frac{1}{N}\sum_{i=1}^{N}|x_{i,\text{calc}} - x_{i,\text{ref}}|$

**均方根误差（RMSE）**：$\text{RMSE} = \sqrt{\frac{1}{N}\sum_{i=1}^{N}(x_{i,\text{calc}} - x_{i,\text{ref}})^2}$

OpenKIM 的 VC 系统还使用了等级评定方式——将误差映射为 A/B/C/D/F 字母等级，这种定性评估对非专业用户更友好。例如，晶格常数的相对偏差 <1% 为 A 级，1-3% 为 B 级，3-5% 为 C 级，5-10% 为 D 级，>10% 为 F 级 [具体阈值需参考 OpenKIM 官方文档确认]。

### 6.4 自动化执行方案

基于 LAMMPS 的自动化验算流程设计如下：

**步骤一：准备势函数文件**。将用户上传的势函数文件（EAM setfl、MEAM 库文件等）转换为 LAMMPS 可读格式，生成 pair_style 和 pair_coeff 命令。

**步骤二：生成计算输入**。对于每种验证属性，使用模板生成 LAMMPS 输入脚本。输入脚本中包含势函数命令、结构定义、计算参数。

**步骤三：并行执行计算**。使用 Python subprocess 或任务队列并行执行多个计算。每个计算任务独立运行，互不依赖（除有明确依赖关系的任务外）。

**步骤四：结果采集**。从 LAMMPS 输出日志中提取计算结果，解析并结构化为 JSON 格式。

**步骤五：对比分析**。从参考数据库中获取对应材料/体系的实验值或 DFT 值，计算误差指标。

**步骤六：生成报告**。将所有计算结果和误差分析汇总为可视化报告。

---

## 7. 跨平台对比分析

| 维度 | OpenKIM | NIST IPR | ML 势生态 |
|------|---------|----------|-----------|
| 评估框架 | Tests + VC 双轨制 | iprPy 22 种计算 | 误差分析 + benchmark |
| 自动化程度 | 全自动 Pipeline | 半自动（需手动触发 iprPy） | 因框架而异 |
| 参考数据 | 内置 Reference Data + DOI | 分散在各势函数页面 | 标准数据集（revMD17 等） |
| 接口标准化 | KIM API 统一接口 | 文件格式标准化 | ASE Calculator 接口 |
| 可引用性 | DOI + Web of Science 索引 | 文献引用 | 模型 + 数据集引用 |
| 代码完整性检查 | VC 三级分类（A-F 评分） | 无 | 等变性测试等 |
| 扩展性 | Test Driver 机制 | iprPy 模块化设计 | 插件式架构 |
| 社区参与 | 开放贡献 Tests/VCs | 邮件提交 | 开源框架 |

OpenKIM 的优势在于评估框架的系统性和自动化程度最高，Pipeline 在云端全自动运行，无需用户干预。NIST IPR 的优势在于 iprPy 的 22 种计算方法覆盖面广，特别在缺陷性质计算方面（位错、层错、点缺陷）更为深入。ML 势生态的优势在于精度评估更为精细（逐构型对比、不确定性量化），但缺乏统一的评估平台。

---

## 8. 对 NucPot 的设计建议

### 8.1 三级验证策略

建议 NucPot 采用三级验证策略，与势函数的成熟度和用户需求相匹配：

**第一级：基础验证（自动触发，每次上传时执行）**

当用户上传势函数后，系统自动执行以下计算：
- 孤立原子能量计算（确保势函数可以正确处理单原子系统）
- 能量-距离曲线扫描（检测势函数在短距离处的行为是否合理）
- 晶格常数和结合能（弛豫后与实验值对比）
- 弹性常数（C11、C12、C44）

这级验证不需要用户干预，也不需要大量计算资源，可以在秒到分钟级别完成。

**第二级：标准验证（审核时执行，贡献者或审核员触发）**

在势函数通过初步审核后，执行更全面的计算：
- 体模量、剪切模量、杨氏模量
- 空位形成能和间隙原子形成能
- 表面能（低指数晶面）
- 层错能（如适用）
- 相稳定性（与竞争相的能量对比）

这级验证需要更多的计算时间（分钟到小时级别），但覆盖了用户选择势函数时最关心的性质。

**第三级：全面验证（专家评估，按需执行）**

针对高价值势函数或特定应用场景：
- 熔点预测（NPT 升温或固-液共存法）
- 声子谱（与实验对比）
- 自由能计算（相图预测）
- 扩散系数和迁移能垒
- 高温行为评估
- 辐照效应相关性质（如 PKA 阈值位移能）[NucPot 特有需求]

### 8.2 核材料特殊考虑

NucPot 作为核材料领域的势函数库，需要考虑以下特殊验证需求：

**高温行为**。核材料在运行温度下（如 UO₂ 燃料中心温度可达 2000°C）的性能评估至关重要。势函数在高温下的行为（如热膨胀、比热容、热导率）应与实验数据对比。

**辐照效应**。核材料在辐照环境下的行为是关注焦点。虽然势函数本身不直接模拟辐照损伤，但势函数预测的点缺陷性质（形成能、迁移能垒）、离位阈能等是辐照损伤模拟的关键输入。

**相稳定性**。核燃料在运行过程中会经历复杂的相变（如 U-Zr 合金的 γ 相稳定性）。势函数对相稳定性的预测能力是评估其适用性的重要指标。

**多组元体系**。核材料通常是多组元合金（如 U-Pu-Zr 金属燃料、Zr-Sn-Nb-Fe 包壳合金）。势函数在多组元体系中的传递性（即二元势函数预测三元行为的能力）需要特别验证。

### 8.3 技术实现路线图

**Phase 1（MVP，1-2 月）：LAMMPS + ASE 基础管线**

- 使用 ASE 的 LAMMPS Calculator 接口封装计算引擎
- 实现 4-5 种基础属性计算（晶格常数、结合能、弹性常数、体模量、空位形成能）
- 从 LAMMPS 输出中解析结果
- 与硬编码的实验参考值对比
- 在势函数详情页显示验证结果

**Phase 2（标准，3-6 月）：扩展计算 + 参考数据库**

- 扩展到 15+ 种计算属性
- 建立核材料参考数据库（实验值 + DFT 值）
- 实现误差评分和等级评定（借鉴 OpenKIM 的 A-F 体系）
- 生成可视化对比图表
- 实现异步计算队列（用户上传后后台执行）

**Phase 3（全面，6-12 月）：Pipeline + ML 验证**

- 实现完整的 Processing Pipeline（依赖管理、并行调度、错误处理）
- 集成 ML 势函数的验证工具（误差分析、不确定性评估）
- 开发核材料特有的验证模块（辐照效应、高温行为）
- 支持用户自定义验证任务

---

## 9. 结论

势函数质量评估是确保原子模拟可靠性的关键环节。OpenKIM 通过 KIM Tests/Test Drivers + Verification Checks + Processing Pipeline 的架构实现了当前最完善的自动化评估体系，其 Crystal Genome 框架正在将评估能力扩展到所有已知晶体结构。NIST IPR 的 iprPy 提供了 22 种标准化计算方法，特别在缺陷性质方面覆盖深入。机器学习势函数领域发展出了基于标准 benchmark 数据集的统一评估范式，其逐构型精度分析和不确定性量化方法值得传统势函数评估借鉴。

对于 NucPot 平台，建议采用渐进式实现策略：先建立基于 LAMMPS + ASE 的基础验证管线，覆盖晶格常数、结合能、弹性常数等核心属性；再逐步扩展到缺陷性质和高温行为等核材料特有需求。评估框架应借鉴 OpenKIM 的 Test Driver 模式实现评估方法的模块化和可扩展性，同时参考其等级评定体系（A-F）为用户提供直观的质量指标。

---

## 10. 参考文献

1. OpenKIM About Page. https://openkim.org/about/
2. OpenKIM Documentation — Introduction to KIM Tests. https://openkim.org/doc/evaluation/kim-tests/
3. OpenKIM Documentation — Introduction to Verification Checks. https://openkim.org/doc/evaluation/kim-verification-checks/
4. OpenKIM Documentation — Types of KIM Content. https://openkim.org/doc/repository/kim-content/
5. OpenKIM Documentation — Using KIM Content. https://openkim.org/doc/usage/overview/
6. kimvv — KIM Validation and Verification Python Package. https://github.com/openkim/kimvv
7. iprPy — NIST Interatomic Potential Repository Property Calculation Tools. https://github.com/usnistgov/iprPy
8. iprPy Documentation — Calculation Styles. https://www.ctcms.nist.gov/potentials/iprPy/calculation_styles.html
9. NIST Interatomic Potentials Repository. https://www.ctcms.nist.gov/potentials/
10. Tadmor, E. B., Elliott, R. S., & Miller, R. E. OpenKIM: An online framework for making molecular simulations reliable and reproducible. http://vimeo.com/103442960
11. Nikiforov, I. Crystal Genome Material Property Computation Framework. LAMMPS Workshop Aug 2025. https://www.lammps.org/workshops/Aug25/talk/ilia-nikiforov/
12. kim-tools Documentation. https://kim-tools.readthedocs.io
13. KIM Developer Platform. https://github.com/openkim/developer-platform
14. ASE — Atomic Simulation Environment. https://wiki.fysik.dtu.dk/ase/
15. Batatia, I. et al. MACE: Higher-order equivariant message passing for fast and accurate force fields. (MACE ML Potential)
16. Batzner, S. et al. NequIP: E(3)-equivariant neural network potentials. Nature Communications, 2022.
17. Li, L. et al. FLARE: Fast Learning of Atomistic Rare Events. npj Computational Materials, 2022.
18. Wang, H. et al. DeepMD-kit: A deep learning package for many-body observable and molecular dynamics. Computer Physics Communications, 2021.
