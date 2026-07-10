"""NucMat ontology → LightRAG extraction prompt builder.

Converts the NucMat domain ontology (kg_entity_types, kg_relation_types)
into LightRAG custom extraction prompts so the LLM recognizes nuclear
materials entities and relations instead of generic ones.

Uses LightRAG's built-in ``addon_params`` customization mechanism —
does NOT fork or modify LightRAG source code.

NFM-750 — NFM-741.3: NucMat ontology injection into LightRAG prompts.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol: any object exposing the fields we read from KEntityType / KRelationType
# ---------------------------------------------------------------------------


@runtime_checkable
class EntityTypeRow(Protocol):
    """Minimal interface for an entity type record."""

    name: str
    label_template: str | None
    required_properties: list[str] | None
    description: str | None


@runtime_checkable
class RelationTypeRow(Protocol):
    """Minimal interface for a relation type record."""

    name: str
    source_types: list[str] | None
    target_types: list[str] | None
    properties_schema: dict[str, Any] | None
    description: str | None


# ---------------------------------------------------------------------------
# Bilingual entity type descriptions (used when DB description is None)
# ---------------------------------------------------------------------------

_FALLBACK_ENTITY_CN: dict[str, str] = {
    "Material": "核燃料材料/化合物",
    "Property": "可测量的物理或化学性质",
    "Experiment": "实验研究或测量",
    "Condition": "实验条件或参数",
    "Publication": "科学出版物或报告",
}


_FALLBACK_RELATION_CN: dict[str, str] = {
    "hasProperty": "材料具有某种性质",
    "measuredIn": "实验测量了某种材料",
    "hasCondition": "实验在某种条件下进行",
    "cites": "出版物引用另一出版物",
    "extractsFrom": "从出版物中提取数据",
    "relatedTo": "两种材料相关联",
    "composedOf": "材料由另一种材料组成",
    "produces": "实验产生某种材料",
    "investigates": "实验研究某种性质",
    "performedAt": "实验在特定条件下执行",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_entity_extraction_prompt(
    entity_types: list[EntityTypeRow],
) -> str:
    """Build a system prompt for the EXTRACT role defining all NucMat entity types.

    Parameters
    ----------
    entity_types:
        Rows from ``kg_entity_types`` (ORM objects or compatible stubs).

    Returns
    -------
    str
        Bilingual prompt (English + Chinese) listing every entity type
        with its description and label template.
    """
    if not entity_types:
        return (
            "Extract entities from the text. / 从文本中提取实体。"
            "\nNo entity types defined. / 未定义实体类型。"
        )

    lines: list[str] = [
        "You are an expert in nuclear fuel materials (核燃料材料领域专家).",
        "",
        "Extract entities from the given text according to these types:",
        "请根据以下实体类型从给定文本中提取实体:",
        "",
    ]

    for et in entity_types:
        desc_en = et.description or ""
        desc_cn = _FALLBACK_ENTITY_CN.get(et.name, "")
        template = et.label_template or ""
        props = ", ".join(et.required_properties) if et.required_properties else ""

        lines.append(f"## {et.name} ({desc_cn})")
        lines.append(f"   English: {desc_en}")
        if template:
            lines.append(f"   Label template: {template}")
        if props:
            lines.append(f"   Required properties: {props}")
        lines.append("")

    return "\n".join(lines)


def get_relation_extraction_prompt(
    relation_types: list[RelationTypeRow],
) -> str:
    """Build a prompt for the EXTRACT role defining all NucMat relation types.

    Parameters
    ----------
    relation_types:
        Rows from ``kg_relation_types`` (ORM objects or compatible stubs).

    Returns
    -------
    str
        Bilingual prompt listing every relation type with source/target
        constraints and JSON Schema for properties.
    """
    if not relation_types:
        return (
            "Extract relations between entities. / 从实体间提取关系。"
            "\nNo relation types defined. / 未定义关系类型。"
        )

    lines: list[str] = [
        "Identify relations between extracted entities:",
        "请识别已提取实体之间的关系:",
        "",
    ]

    for rt in relation_types:
        desc_en = rt.description or ""
        desc_cn = _FALLBACK_RELATION_CN.get(rt.name, "")
        sources = ", ".join(rt.source_types) if rt.source_types else "Any"
        targets = ", ".join(rt.target_types) if rt.target_types else "Any"
        schema = json.dumps(rt.properties_schema, ensure_ascii=False) if rt.properties_schema else ""

        lines.append(f"## {rt.name} ({desc_cn})")
        lines.append(f"   English: {desc_en}")
        lines.append(f"   Source types: {sources}")
        lines.append(f"   Target types: {targets}")
        if schema:
            lines.append(f"   Properties JSON Schema: {schema}")
        lines.append("")

    return "\n".join(lines)


def build_lightrag_config(
    entity_types: list[EntityTypeRow],
    relation_types: list[RelationTypeRow],
) -> dict[str, str]:
    """Merge ontology prompts into a LightRAG ``addon_params`` dict.

    Returns a dict with keys that can be spread directly into
    LightRAG's ``addon_params``::

        addon_params = {"language": "English"}
        addon_params.update(build_lightrag_config(entities, relations))

    Parameters
    ----------
    entity_types:
        Rows from ``kg_entity_types``.
    relation_types:
        Rows from ``kg_relation_types``.

    Returns
    -------
    dict
        ``{"entity_types_guidance": ..., "relation_types_guidance": ...}``
    """
    return {
        "entity_types_guidance": get_entity_extraction_prompt(entity_types),
        "relation_types_guidance": get_relation_extraction_prompt(relation_types),
    }
