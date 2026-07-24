"""Pydantic models for OntoFuel material_ontology_enhanced.json (NFM-1820).

Covers the 6 top-level model types plus the document envelope.
Source JSON is a dict-of-dicts format (keys are local names, values
are full attribute records).

Individuals are heterogeneous — their data properties vary by class —
so ``OntologyIndividual`` uses ``extra="allow"`` to capture them all
without enumerating every possible property.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RdfsLabel(BaseModel):
    """An RDF label with optional language tag."""

    value: str
    lang: str = ""


class OntologyMetadata(BaseModel):
    """Top-level ontology metadata block.

    The source JSON contains additional integration-tracking fields
    (hea_diffusion_integration, uhea_integration, etc.) that are
    not part of the core contract — they are preserved via extra="allow".
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    version: str
    namespace: str
    created: datetime
    modified: datetime
    last_updated: datetime | None = None
    individuals_count: int = 0
    source: str


class OntologyClass(BaseModel):
    """An OWL class definition."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    uri: str = Field(min_length=1)
    type: str = Field(min_length=1)
    rdfs_label: list[RdfsLabel] = Field(default_factory=list, alias="rdfs:label")
    rdfs_comment: str | None = Field(default=None, alias="rdfs:comment")
    comment: str | None = None
    parent: str | None = None


class ObjectProperty(BaseModel):
    """An OWL object property.

    In source data, ``domain`` and ``range`` can be a plain string, a list
    of strings, or a list of dicts with a ``uri`` key.  ``uri`` and ``type``
    are normally present but some entries only have ``comment``/``domain`` —
    they default to empty string.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    uri: str = ""
    type: str = ""
    rdfs_label: list[RdfsLabel] = Field(default_factory=list, alias="rdfs:label")
    rdfs_comment: str | None = Field(default=None, alias="rdfs:comment")
    domain: str | list[str] | list[dict[str, str]] | None = None
    range: str | list[str] | list[dict[str, str]] | None = None


class DataProperty(BaseModel):
    """An OWL datatype property.

    Same flexibility as ObjectProperty — ``uri`` and ``type`` default to
    empty string for entries that only carry a comment/domain.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    uri: str = ""
    type: str = ""
    rdfs_label: list[RdfsLabel] = Field(default_factory=list, alias="rdfs:label")
    rdfs_comment: str | None = Field(default=None, alias="rdfs:comment")
    domain: str | list[str] | list[dict[str, str]] | None = None


class OntologyIndividual(BaseModel):
    """An ontology individual / instance.

    The ``type`` field in source data can be a URI string, a list of
    class names, or absent (None).  Many individuals (633 of 755) lack
    a ``uri`` field entirely — they carry ``label``, ``description``,
    ``properties``, etc. instead.  ``uri`` defaults to empty string.
    """

    model_config = ConfigDict(extra="allow")

    uri: str = ""
    type: str | list[str] | None = None


class MaterialOntologyDocument(BaseModel):
    """Root document envelope for material_ontology_enhanced.json."""

    model_config = ConfigDict(populate_by_name=True)

    metadata: OntologyMetadata
    classes: dict[str, OntologyClass]
    object_properties: dict[str, ObjectProperty] = Field(alias="objectProperties")
    datatype_properties: dict[str, DataProperty] = Field(alias="datatypeProperties")
    individuals: dict[str, OntologyIndividual]
