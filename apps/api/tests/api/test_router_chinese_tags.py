"""Verify all v1 routers carry Chinese tags for /docs Swagger UI.

Uses AST parsing to avoid importing modules that depend on missing
model files (pre-existing breakage on this branch).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_V1_DIR = Path(__file__).resolve().parents[2] / "src" / "nfm_db" / "api" / "v1"

# (filename, expected_tag)
ROUTER_SPECS: list[tuple[str, str]] = [
    ("auth_endpoints.py", "认证管理"),
    ("materials.py", "材料管理"),
    ("properties.py", "属性管理"),
    ("sources.py", "数据源管理"),
    ("reference_values.py", "参考值管理"),
    ("reference_gaps.py", "参考缺口管理"),
    ("literature.py", "文献管理"),
    ("review.py", "评审管理"),
    ("extraction.py", "提取管理"),
    ("conflict.py", "冲突管理"),
    ("kg.py", "知识图谱"),
    ("ontology.py", "本体管理"),
    ("potentials.py", "势函数管理"),
    ("viz.py", "可视化"),
    ("health.py", "健康检查"),
    ("feedback.py", "反馈管理"),
    ("blog.py", "博客管理"),
    ("md_verification.py", "MD验证"),
    ("verification.py", "数据验证"),
]


def _extract_router_tags(filename: str) -> list[str]:
    """Parse the router file and extract the tags list from APIRouter()."""
    source = (_V1_DIR / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "router":
                    if isinstance(node.value, ast.Call):
                        call = node.value
                        # Check if it's APIRouter(...)
                        func_name = call.func.id if isinstance(call.func, ast.Name) else None
                        if func_name == "APIRouter":
                            for keyword in call.keywords:
                                if keyword.arg == "tags" and isinstance(keyword.value, ast.List):
                                    return [
                                        elt.value
                                        for elt in keyword.value.elts
                                        if isinstance(elt, ast.Constant)
                                    ]
    return []


@pytest.mark.parametrize("filename,expected_tag", ROUTER_SPECS)
def test_router_has_chinese_tag(filename: str, expected_tag: str) -> None:
    """Every v1 router must declare the expected Chinese tag."""
    tags = _extract_router_tags(filename)
    assert expected_tag in tags, f"{filename}: expected tag '{expected_tag}' in {tags!r}"


@pytest.mark.parametrize("filename,expected_tag", ROUTER_SPECS)
def test_router_tags_non_empty(filename: str, expected_tag: str) -> None:
    """Every v1 router must have at least one tag."""
    tags = _extract_router_tags(filename)
    assert len(tags) >= 1, f"{filename}: router.tags is empty"
