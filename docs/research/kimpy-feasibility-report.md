# KIM/ASE 技术可行性报告 — NucPot 验证管线

> **结论：可行。** ASE 3.28.0 + kimpy 2.1.4 + KIM API 2.4.2 均可用，可直接构建势函数性质计算管线。

## 环境信息

| 组件 | 版本 | 状态 |
|------|------|------|
| Python | 3.11.5 | ✅ |
| ASE | 3.28.0 | ✅ EAM calculator 可用 |
| kimpy | 2.1.4 | ✅ 需 `source ~/.local/kim-api/bin/kim-api-activate` |
| KIM API | 2.4.2 | ✅ `~/.local/kim-api/` |
| kimvv | — | 未安装 (可选) |
| LAMMPS | — | 未安装 |

## 可行性验证结果

### 1. ASE 内置 EAM 计算器
- `ase.calculators.eam.EAM` 可用
- `ase.build.bulk()` 支持 BCC/FCC/HCP/SC/Diamond 结构
- `ase.optimize.BFGS` 可执行结构弛豫
- 能量/力/应力计算均正常

### 2. KIM/ASE 集成
- kimpy 2.1.4 可导入（需先 activate KIM API 环境）
- `ase.calculators.kim.KIM` 可作为 ASE calculator 使用
- 支持 OpenKIM 模型库中的所有模型

### 3. 计算能力评估

| 性质 | 方法 | 可行性 |
|------|------|--------|
| 晶格常数 | bulk() + BFGS 弛豫 + volume→a | ✅ |
| 弹性常数 | Birch-Murnaghan EOS 拟合 | ✅ (bulk modulus) |
| 空位形成能 | E_vac - (N-1)/N * E_perfect | ✅ |
| 内聚能 | E_atom - E_bulk/N | ✅ |

### 4. 限制
- **C11/C12/C44 独立弹性常数**：需要施加特定应变张量，Birch-Murnaghan 只给 bulk modulus。MVP 阶段先实现 bulk modulus，后续增加完整弹性常数矩阵。
- **MEAM 势函数**：ASE 不原生支持 MEAM pair_style，需要 KIM 接口或 LAMMPS Python 包装器。
- **速度**：EAM 计算 BCC 金属单胞约 <1s，适合在线服务。

## 推荐方案

**MVP 阶段（Phase 1）：**
- ASE 作为默认计算引擎
- 支持 EAM/FS 格式势函数
- 计算晶格常数、bulk modulus、内聚能、空位形成能
- 评分系统（A-F）

**后续扩展（Phase 2）：**
- KIM 集成支持更多势函数类型（MEAM、ML 等）
- LAMMPS Python 包装器用于复杂体系
- 完整弹性常数矩阵 (C11, C12, C44)

## 自动激活配置

Docker/服务启动时需执行：
```bash
source ~/.local/kim-api/bin/kim-api-activate
```

或通过环境变量：
```bash
export PKG_CONFIG_PATH=~/.local/lib/pkgconfig:${PKG_CONFIG_PATH}
export LD_LIBRARY_PATH=~/.local/lib:${LD_LIBRARY_PATH}
```
