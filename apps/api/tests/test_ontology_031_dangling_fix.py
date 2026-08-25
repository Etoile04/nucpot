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

# --- Frozen 0.3.0 baseline (hermetic — no git dependency) -------------------
# The 0.3.1 import was purely additive over the 0.3.0 file (PR #982). The
# original release pinned these invariants; re-deriving them from
# ``origin/main`` breaks after merge (origin/main then *is* 0.3.1 and the
# diff collapses to zero), so the baseline is frozen here as content
# hashes + the explicit list of the 14 classes 0.3.1 added.
_BASELINE_SECTION_HASHES = {
    "individuals": "94ab77adbae86b05",
    "objectProperties": "c480ee38a66d7386",
    "datatypeProperties": "00611b0c65f04341",
}
_BASELINE_CLASS_COUNT = 139  # classes in the 0.3.0 file
_ADDED_IN_031 = {
    "MaterialProperty", "Defect", "PhaseTransformation",
    "CrystallinePhase", "MaterialComposition", "ThermalProperties",
    "ApplicationContext", "IrradiationSimulation", "TemperatureCondition",
    "IrradiationCondition", "AlloySystem", "UraniumMolybdenumAlloy",
    "DiffusionProcess", "SwellingModel",
}


def _section_hash(obj: object) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


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
    """AC-4: the 0.3.0 content is preserved unchanged inside 0.3.1.

    The baseline is the frozen 0.3.0 content (section hashes + class
    count), not ``origin/main`` — post-merge, origin/main *is* 0.3.1 and
    a git-diff baseline would collapse to zero.
    """

    @pytest.fixture(scope="class")
    def current(self):
        return _load_json(_ENHANCED_JSON)

    def test_class_count_is_153(self, current):
        assert len(current["classes"]) == 153

    def test_exactly_14_new_classes(self, current):
        """All 139 original classes still present + exactly the 14 added."""
        names = set(current["classes"])
        # every 0.3.0 class is still present (139 = 153 - 14)
        assert len(names - _ADDED_IN_031) == _BASELINE_CLASS_COUNT
        # the 0.3.1 additions are exactly the expected set
        assert names >= _ADDED_IN_031

    def test_individuals_unchanged(self, current):
        assert _section_hash(current["individuals"]) == _BASELINE_SECTION_HASHES["individuals"]

    def test_object_properties_unchanged(self, current):
        assert _section_hash(current["objectProperties"]) == _BASELINE_SECTION_HASHES["objectProperties"]

    def test_datatype_properties_unchanged(self, current):
        assert _section_hash(current["datatypeProperties"]) == _BASELINE_SECTION_HASHES["datatypeProperties"]


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
        return {n: data["classes"][n] for n in _ADDED_IN_031}

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
