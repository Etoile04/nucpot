# ruff: noqa: RUF001  # _V1_PROMPT_TEMPLATE uses full-width CJK punctuation — this
# is a faithful reproduction of the pre-NFM-3258 V1 prompt, which used Chinese
# punctuation. Do NOT "fix" this; that would alter the prompt text and break the
# parity comparison against V2.
"""Reconstructed V1 legacy prompt path for the parity harness (NFM-3581).

Mirrors the pre-NFM-3258 behavior of `extraction_prompt.build_extraction_system_prompt()`:
- 11 fixed `PropertyCategory` enum values as categories
- `STANDARD_PROPERTIES` mapping (property_mapping.json) as the standard name source
- No ontology context block (the V1 path ignored ontology_data entirely)

This module exists ONLY to feed the snapshot-diff harness. It is NOT used by
the production pipeline — NFM-3258 removed the legacy path and NFM-3580 turned
`STANDARD_PROPERTIES` into a backward-compat shim.

Why the V1 path is reconstructed (not imported):
- `extraction_prompt.build_extraction_system_prompt()` was deleted in NFM-3258
- `STANDARD_PROPERTIES` (post-NFM-3580) is a shim that returns the ontology's
  alias mapping, not the original hardcoded JSON config
- We need a deterministic V1 path that produces the SAME prompt every time,
  independent of which ontology version is active

The implementation here intentionally mirrors the pre-NFM-3258 logic exactly
(see git history: 5d8ad11a^:apps/api/src/nfm_db/services/extraction_prompt.py).
Any drift between this and the historical V1 path is a bug — file an issue
against this module rather than adjusting the assertion to match.
"""

from __future__ import annotations

from typing import Any

from nfm_db.core.property_catalog import (
    STANDARD_PROPERTIES,
    PropertyCategory,
)


def _build_categories_block() -> str:
    """Build the 11 property categories block from the live enum.

    Mirrors pre-NFM-3258 behavior: 9 core categories, 2 supporting.
    """
    all_categories = list(PropertyCategory)
    assert len(all_categories) == 11, (
        f"PropertyCategory count changed ({len(all_categories)}); "
        "review core/supporting boundary in _build_categories_block"
    )
    core = all_categories[:9]
    supporting = all_categories[9:]
    lines = ["## Property Categories (property_category)", ""]
    for cat in core:
        lines.append(f"- {cat.value} [核心]")
    for cat in supporting:
        lines.append(f"- {cat.value} [支持]")
    return "\n".join(lines)


def _build_standard_names_block() -> str:
    """Build representative standard property names from the hardcoded mapping.

    Mirrors pre-NFM-3258 behavior: deduplicate standard-name values, sort.
    """
    # Collect unique standard names (values), deduplicate
    seen: set[str] = set()
    names: list[str] = []
    for standard_name in STANDARD_PROPERTIES.values():
        if standard_name not in seen:
            seen.add(standard_name)
            names.append(standard_name)
    # Sort for determinism
    names.sort()
    lines = [
        "## Standard Property Names (property)",
        "优先使用以下标准名称:",
        "",
    ]
    for name in names:
        lines.append(f"- {name}")
    return "\n".join(lines)


# Same template body as V2 — only the dynamic blocks differ. Inlined to keep
# the V1 path independent of any later changes to _PROMPT_TEMPLATE.
_V1_PROMPT_TEMPLATE = """\
# 核材料性能数据抽取系统 v4

你是核材料性能数据抽取专家。使用LLM语义理解从Markdown文献中抽取结构化性能数据。
禁止仅用正则/关键词匹配——必须语义判断材料、条件、性能归属。
必须扫描全文：Abstract、正文、表格、图注、Discussion、Appendix。

## 输出格式

返回JSON数组。每条记录字段固定顺序：
source_file → material_name → composition → phase → element →
property_category → property → value → unit → conditions →
context → confidence → reference

```json
{{
  "source_file": "md_output/volume_015/paper.md",
  "material_name": "Zr-2.5Nb",
  "composition": "Zr-2.5Nb",
  "phase": "oxide",
  "element": null,
  "property_category": "腐蚀",
  "property": "氧化膜厚度",
  "value": "3 to 4",
  "unit": "μm",
  "conditions": {{
    "condition_type": "service",
    "temp_C": 300
  }},
  "context": null,
  "confidence": "high",
  "reference": "Author, Paper Title"
}}
```

{categories_block}

{standard_names_block}

## 字段规则

- material_name: 材料名称/合金牌号。Zr-2.5Nb类名称本身含成分时composition同填
- composition: 仅来自原文或材料名本身，禁止凭外部常识补全
- phase: 按物相规则标准化（alpha/beta/gamma等），不确定填null
- element: 与性能直接相关的元素，无则null
- value: 字符串，保留原文精度、范围和科学计数法
- unit: 单位，无量纲填"dimensionless"
- conditions: 必须含condition_type (experimental|simulation|service|processing|mixed|unknown)，其他按需填temp_C/pressure_MPa/dpa/fluence_n_m2/burnup_GWd_t/atmosphere/simulation_method等
- confidence: high（信息完整）/ medium（缺少phase/conditions）/ low（仅property+value+unit）
- reference: 作者, 文章标题（当前论文，非被引文献）

## 多论文文件

一个Markdown文件可能含多篇论文。按REFERENCE行分篇，逐篇独立抽取，同一文件内不同论文共用source_file。

## 抽取优先级

1. 9个核心property_category的性能数据
2. 核心性能的模型/拟合参数
3. 支持性材料状态信息（成分、冷加工、晶粒尺寸等）
4. 其他可判断的材料数据
同处数据可作条件又可作性能时，优先作为conditions。

## 不抽取

- 无具体数字的定性描述
- 图号/表号/样品编号
- 无材料对象也无物相对象的孤立数值
- 二手引用数据（除非明确要求汇总）
- 无明确材料/条件/单位的模型假设值
- 纯测试条件作为独立记录
- 反应堆系统几何参数（非直接描述被测材料样品）

## 自检门控（输出前检查）

1. source_file是否填写
2. material_name是否尽可能填写
3. composition是否仅来自原文
4. property_category是否属于固定枚举
5. 核心性能是否正确归入9个类别
6. 制备温度/冷加工/样品尺寸是否误归为核心性能
7. phase是否跟测量对象走
8. value是否保留原文精度
9. conditions是否仅填与该数据点直接相关的条件
10. reference是否为当前论文

仅返回合法JSON数组，不要返回其他内容。"""


def build_v1_legacy_prompt(ontology_data: dict[str, Any] | None) -> str:
    """Build the V1-hardcoded extraction prompt (legacy V1 path).

    Accepts ontology_data to match V2's call shape, but ignores it — V1 was
    hardcoded and never read ontology_data. The argument exists so the harness
    can pass identical inputs to both paths.

    Returns:
        Complete V1 extraction prompt string.

    Raises:
        ValueError: never. V1 had no validation; it always returned a string.
    """
    del ontology_data  # V1 never read ontology_data
    categories_block = _build_categories_block()
    standard_names_block = _build_standard_names_block()
    return _V1_PROMPT_TEMPLATE.format(
        categories_block=categories_block,
        standard_names_block=standard_names_block,
    )
