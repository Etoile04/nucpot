"""Tests for ontology 0.3.1 — 14 dangling-type class additions (NFM-3715).

Gates:

1. **Zero dangling** — every individual type short-name resolves to a class
   in the 0.3.1 JSON, *except* ``owl:NamedIndividual`` (OWL built-in, 64
   refs).  This is AC-1 + AC-2 + AC-3.
2. **Additivity** — the 139 original classes, all individuals, all
   objectProperties, and all datatypeProperties are byte-identical to
   the 0.3.0 file on ``origin/main``.  AC-4.
3. **Class count** — 139 + 14 = 153 classes total.
4. **Version** — metadata.version == "0.3.1".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nfm_db.services.ontology_import import (
    build_enhanced_layer,
    load_enhanced_document,
)

_DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "nfm_db" / "data"
_ENHANCED_JSON = _DATA_DIR / "material_ontology_enhanced.json"
_OWL_NAMED_INDIVIDUAL = "owl:NamedIndividual"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _short_name(uri_or_name: str) -> str:
    """Extract short name from full URI or bare name."""
    if "#" in uri_or_name:
        return uri_or_name.split("#")[-1]
    if "/" in uri_or_name:
        return uri_or_name.split("/")[-1]
    return uri_or_name


class TestZeroDangling:
    """AC-1 / AC-2: every individual type resolves to a class."""

    @pytest.fixture(scope="class")
    def data(self):
        return _load_json(_ENHANCED_JSON)

    def test_no_dangling_types_except_owl_named_individual(self, data):
        class_names = set(data["classes"].keys())
        dangling: list[tuple[str, str]] = []

        for key, ind in data["individuals"].items():
            t = ind.get("type", "")
            if not t:
                continue
            types = t if isinstance(t, list) else [t]
            for ti in types:
                short = _short_name(ti)
                if short != _OWL_NAMED_INDIVIDUAL and short not in class_names:
                    dangling.append((short, key))

        if dangling:
            from collections import Counter
            counts = Counter(name for name, _ in dangling)
            detail = "; ".join(f"{n}({c})" for n, c in counts.most_common())
            pytest.fail(f"Dangling types found: {detail}")

    def test_owl_named_individual_not_a_class(self, data):
        assert _OWL_NAMED_INDIVIDUAL not in data["classes"]

    def test_owl_named_individual_ref_count(self, data):
        """AC-3: 64 individuals reference owl:NamedIndividual."""
        count = 0
        for ind in data["individuals"].values():
            t = ind.get("type", "")
            if not t:
                continue
            types = t if isinstance(t, list) else [t]
            for ti in types:
                if _short_name(ti) == _OWL_NAMED_INDIVIDUAL:
                    count += 1
        assert count == 64, f"Expected 64 owl:NamedIndividual refs, got {count}"


class TestAdditivity:
    """AC-4: 139 original classes and all other sections unchanged."""

    @pytest.fixture(scope="class")
    def current(self):
        return _load_json(_ENHANCED_JSON)

    @pytest.fixture(scope="class")
    def original_139(self):
        """Load 0.3.0 from origin/main as the baseline."""
        import subprocess
        result = subprocess.run(
            ["git", "show", "origin/main:apps/api/src/nfm_db/data/material_ontology_enhanced.json"],
            capture_output=True,
            text=True,
            cwd=str(_DATA_DIR.parents[1]),
        )
        if result.returncode != 0:
            pytest.skip("Cannot read origin/main baseline")
        return json.loads(result.stdout)

    def test_class_count_is_153(self, current):
        assert len(current["classes"]) == 153

    def test_exactly_14_new_classes(self, current, original_139):
        added = set(current["classes"]) - set(original_139["classes"])
        assert len(added) == 14
        expected = {
            "MaterialProperty", "Defect", "PhaseTransformation",
            "CrystallinePhase", "MaterialComposition", "ThermalProperties",
            "ApplicationContext", "IrradiationSimulation", "TemperatureCondition",
            "IrradiationCondition", "AlloySystem", "UraniumMolybdenumAlloy",
            "DiffusionProcess", "SwellingModel",
        }
        assert added == expected

    def test_original_139_classes_unchanged(self, current, original_139):
        for name in original_139["classes"]:
            assert current["classes"][name] == original_139["classes"][name], (
                f"Class {name!r} was modified"
            )

    def test_individuals_unchanged(self, current, original_139):
        assert current["individuals"] == original_139["individuals"]

    def test_object_properties_unchanged(self, current, original_139):
        assert current["objectProperties"] == original_139["objectProperties"]

    def test_datatype_properties_unchanged(self, current, original_139):
        assert current["datatypeProperties"] == original_139["datatypeProperties"]


class TestMetadata:
    """Version bump to 0.3.1."""

    @pytest.fixture(scope="class")
    def data(self):
        return _load_json(_ENHANCED_JSON)

    def test_version_is_031(self, data):
        assert data["metadata"]["version"] == "0.3.1"


class TestNewClassesSchema:
    """Every new class has the required fields."""

    REQUIRED_KEYS = {"uri", "type", "parent", "comment"}

    @pytest.fixture(scope="class")
    def new_classes(self):
        data = _load_json(_ENHANCED_JSON)
        import subprocess
        result = subprocess.run(
            ["git", "show", "origin/main:apps/api/src/nfm_db/data/material_ontology_enhanced.json"],
            capture_output=True, text=True,
            cwd=str(_DATA_DIR.parents[1]),
        )
        if result.returncode != 0:
            pytest.skip("Cannot read origin/main baseline")
        orig = json.loads(result.stdout)
        added_names = set(data["classes"]) - set(orig["classes"])
        return {n: data["classes"][n] for n in added_names}

    def test_all_new_classes_have_required_keys(self, new_classes):
        for name, cls in new_classes.items():
            missing = self.REQUIRED_KEYS - set(cls.keys())
            assert not missing, f"{name} missing keys: {missing}"

    def test_all_new_classes_are_owl_class(self, new_classes):
        for name, cls in new_classes.items():
            assert cls["type"] == "owl:Class", f"{name} type is {cls['type']!r}"

    def test_all_new_classes_have_ontology_uri(self, new_classes):
        for name, cls in new_classes.items():
            assert "materials/ontology#" in cls["uri"], f"{name} uri: {cls['uri']!r}"

    def test_all_new_classes_have_label(self, new_classes):
        for name, cls in new_classes.items():
            labels = cls.get("rdfs:label", [])
            assert isinstance(labels, list) and len(labels) > 0, (
                f"{name} has no rdfs:label"
            )


class TestImportLayerBuilds:
    """The 0.3.1 JSON still parses through the import pipeline."""

    @pytest.fixture(scope="class")
    def layer(self):
        doc = load_enhanced_document(_ENHANCED_JSON)
        return build_enhanced_layer(doc)

    def test_class_count_in_layer_stats(self, layer):
        stats = layer["enhanced_ontology_source"]["counts"]
        assert stats["classes"] == 153

    def test_individual_count_unchanged(self, layer):
        stats = layer["enhanced_ontology_source"]["counts"]
        assert stats["individuals_not_imported"] == 755
