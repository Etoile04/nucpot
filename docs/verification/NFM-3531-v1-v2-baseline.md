# NFM-3531 V1 vs V2 extraction-prompt baseline

Captured **before** [NFM-3531-C](/NFM/issues/NFM-3531) replaces the V2 prompt assembly path. Re-run `pytest apps/api/tests/extraction/test_snapshot_diff.py` on the integrated branch to detect precision/recall regression.

## Inputs

- Ontology fixture: `/Users/lwj04/Projects/nucpot/.worktrees/NFM-3535-snapshot-diff-harness/apps/api/tests/fixtures/extraction/test_ontology_version.json`
- Golden set: `apps/api/tests/fixtures/golden/` (13 fixtures)
- V1 prompt bytes: `4690`
- V2 prompt bytes: `5761`
- Full-prompt byte equality: **`False`**

## Summary

- **Identical sections**: 0 / 2 (0%)
- **Fixtures covered**: 10 / 10 (100%)
- **Categories diverged vs V1**: 0 only-in-V1, 0 only-in-V2, ordering_changed=`False`
- **Standard names diverged vs V1**: 55 only-in-V1, 30 only-in-V2, ordering_changed=`False`
- **Fixtures with category coverage divergence**: 0

## Categories block

- Identical: **`False`**
- V1 size: `305` bytes
- V2 size: `761` bytes

## Standard property names block

- Identical: **`False`**
- V1 size: `1277` bytes
- V2 size: `720` bytes

### Names present in V1 only

- Johnson-Cook参数
- Norton蠕变参数
- Ramberg-Osgood参数
- 位错硬化参数
- 体积模量
- 体积膨胀率
- 体膨胀系数
- 冷加工量
- 吸氘率
- 吸氢率
- 壁厚
- 失效时间
- 定压比热容
- 尺寸变化率
- 屈服准则参数
- 平均空洞尺寸
- 平均线膨胀系数
- 应力指数
- 应力腐蚀阈值
- 延伸率
- 弹性常数
- 强度增量
- 挤压温度
- 断裂应变
- 晶格参数a
- 晶格参数c
- 晶面间距
- 本构模型参数
- 析出相尺寸
- 样品内径
- 样品外径
- 气泡密度
- 气泡肿胀率
- 氘含量
- 氢含量
- 氧化增重
- 流动应力
- 点蚀密度
- 热处理温度
- 热膨胀应变
- 热阻
- 燃料段长度
- 相分数
- 相对密度
- 瞬态蠕变速率
- 瞬时线膨胀系数
- 硬化模量
- 稳态蠕变速率
- 空洞密度
- 空洞肿胀率
- … (+5 more)

### Names present in V2 only

- CTE
- Cp
- HRC
- HV
- TD
- κ
- 体积肿胀
- 光学反射率
- 弹塑性模型
- 沉淀相
- 洛氏硬度
- 烧结密度
- 热传导率
- 热容
- 热电势
- 热膨胀
- 热膨胀系数
- 电阻率
- 相组成
- 硬化性能
- 线膨胀系数
- 织构
- 维氏硬度
- 肿胀百分比
- 腐蚀
- 腐蚀深度
- 蠕变系数
- 辐照肿胀
- 辐照肿胀率
- 辐照蠕变

## Golden-set category coverage

Of the 10 unique `property_category` values declared across the 13 golden-set fixtures:

- V1 enum-driven coverage: **10 / 10**
- V2 ontology-driven coverage: **10 / 10**
- Coverage divergence: **0** category(ies)

No fixture categories diverge between V1 and V2 coverage.

## Delta classification

Categories of delta, with recommended disposition. Anything in the **unacceptable** bucket must be investigated before NFM-3531-C merges into `main`.

| Bucket | Definition | Disposition |
|---|---|---|
| identical | V1 and V2 emit the same bytes for that section. | None — safe to swap. |
| acceptable-extra (V2 only) | V2 surfaces a category that V1's static enum never had. Likely an ontology-specific category the LLM benefits from. | Acceptable if it represents an actual material property class the golden set covers. |
| acceptable-drop (V1 only) | V2 dropped a V1 enum entry because the ontology does not model it. | Acceptable only if the dropped category is unrepresented in the golden set; otherwise flag for LE. |
| ordering-only | Same names, different order. | Cosmetic — does not affect extraction. |
| unacceptable | A category the golden set needs is in one path but not the other. Will cause precision/recall regression. | Block NFM-3531-C until fixed. |

## Verdict for NFM-3531-C merge gate

**PASS-WITH-NOTES** — golden-set coverage matches (10/10), but the prompts diverge in formatting/ordering. NFM-3531-C may merge; document the divergence in the PR description.

## Unified diff (excerpt, first 60 lines)

```diff
--- v1_prompt

+++ v2_prompt

@@ -33,94 +33,103 @@

 ```

 

+## 本体定义 (Ontology)

+

+### 实体类型 (Entity Types)

+

+### UO2

+   Stoichiometric uranium dioxide nuclear fuel matrix.

+   必需属性: 密度, 比热容, 热传导率

+

+### Zr-2.5Nb

+   Zirconium-2.5% niobium pressure-tube alloy (CANDU).

+   必需属性: 密度, 热膨胀, 腐蚀, 弹塑性模型

+

+### Inconel718

+   Ni-based superalloy for high-temperature structural use.

+   必需属性: 密度, 硬化性能, 弹塑性模型

+

+### EUROFER97

+   Reduced-activation ferritic-martensitic steel (fusion blanket).

+   必需属性: 密度, 硬化性能, 辐照肿胀, 辐照蠕变

+

+### SiC-SiC

+   silicon-carbide fibre-reinforced SiC matrix composite.

+   必需属性: 密度, 热传导率, 热膨胀, 弹塑性模型

+

+### Graphite

… (+283 more lines)
```

## Reproducing this baseline

```bash
# From the repo root:
pytest apps/api/tests/extraction/test_snapshot_diff.py -v

# Or via the standalone CLI:
python apps/api/tests/extraction/run_snapshot_diff.py \
    --output docs/verification/NFM-3531-v1-v2-baseline.md
```
