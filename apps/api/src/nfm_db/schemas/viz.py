"""Visualization API schemas for NVL format."""

from pydantic import BaseModel, Field


class Node(BaseModel):
    """NVL node representing an ontology entity."""

    id: str
    name: str
    classes: list[str] = Field(default_factory=list)
    properties: dict[str, str] = Field(default_factory=dict)


class Relationship(BaseModel):
    """NVL relationship between nodes."""

    id: str
    source: str
    target: str
    type: str


class NvlResponse(BaseModel):
    """NVL visualization data response."""

    nodes: list[Node]
    relationships: list[Relationship]


class VizStatsResponse(BaseModel):
    """Visualization statistics response."""

    total_nodes: int
    total_relationships: int
    class_counts: dict[str, int]
