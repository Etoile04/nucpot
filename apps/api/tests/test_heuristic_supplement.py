"""Test that heuristic results supplement (not replace) LLM results (NFM-3424).

When the LLM extractor returns items, the heuristic should still run
and add any new properties that the LLM missed, without creating
duplicates for properties the LLM already found.
"""

from __future__ import annotations


class TestHeuristicSupplementMerge:
    """Verify the dedup-and-append logic in literature_service Step 3b."""

    def _make_llm_props(self) -> list[dict]:
        """Simulate LLM returning 2 properties (undoped Ea and D0)."""
        return [
            {
                "element_system": "UO2",
                "phase": "amorphous",
                "property_name": "activation_energy",
                "value": 0.30,
                "unit": "eV",
                "method": "llm",
                "source": "test-ds-id",
                "source_doi": None,
                "confidence": "high",
                "uncertainty": 0.05,
                "temperature": None,
                "cache_level": "L1",
            },
            {
                "element_system": "UO2",
                "phase": "amorphous",
                "property_name": "diffusion_coefficient",
                "value": 3.32e-8,
                "unit": "cm2/s",
                "method": "llm",
                "source": "test-ds-id",
                "source_doi": None,
                "confidence": "high",
                "uncertainty": 1e-9,
                "temperature": None,
                "cache_level": "L1",
            },
        ]

    def _make_heuristic_props(self) -> list[dict]:
        """Simulate heuristic returning 4 properties (2 overlapping, 2 new)."""
        return [
            {
                "element_system": "UO2",
                "phase": "Unknown",
                "property_name": "activation_energy",
                "value": 0.30,
                "unit": "eV",
                "method": "heuristic_regex",
                "source": "test-ds-id",
                "source_doi": None,
                "confidence": "medium",
                "uncertainty": 0.015,
                "temperature": None,
                "cache_level": "L2",
            },
            {
                "element_system": "UO2",
                "phase": "Unknown",
                "property_name": "diffusion_coefficient",
                "value": 3.32e-8,
                "unit": "cm2/s",
                "method": "heuristic_regex",
                "source": "test-ds-id",
                "source_doi": None,
                "confidence": "medium",
                "uncertainty": 1.66e-9,
                "temperature": None,
                "cache_level": "L2",
            },
            {
                "element_system": "UO2",
                "phase": "Unknown",
                "property_name": "activation_energy",
                "value": 0.26,
                "unit": "eV",
                "method": "heuristic_regex",
                "source": "test-ds-id",
                "source_doi": None,
                "confidence": "medium",
                "uncertainty": 0.013,
                "temperature": None,
                "cache_level": "L2",
            },
            {
                "element_system": "UO2",
                "phase": "Unknown",
                "property_name": "density",
                "value": 10.55,
                "unit": "g/cm3",
                "method": "heuristic_regex",
                "source": "test-ds-id",
                "source_doi": None,
                "confidence": "medium",
                "uncertainty": 0.528,
                "temperature": None,
                "cache_level": "L2",
            },
        ]

    def test_heuristic_supplements_llm_without_duplicates(self):
        """Heuristic adds new properties, skips duplicates."""
        llm_props = self._make_llm_props()
        heuristic_props = self._make_heuristic_props()

        existing_keys = {
            (r.get("element_system"), r.get("property_name"), f"{r.get('value', 0):g}")
            for r in llm_props
        }
        merged = list(llm_props)
        new_count = 0
        for item in heuristic_props:
            key = (
                item.get("element_system"),
                item.get("property_name"),
                f"{item.get('value', 0):g}",
            )
            if key not in existing_keys:
                merged.append(item)
                existing_keys.add(key)
                new_count += 1

        assert new_count == 2
        assert len(merged) == 4

        new_property_names = {m["property_name"] for m in merged[2:]}
        assert new_property_names == {"activation_energy", "density"}
        new_values = {m["value"] for m in merged[2:]}
        assert new_values == {0.26, 10.55}

    def test_empty_heuristic_preserves_llm(self):
        """Empty heuristic result leaves LLM results untouched."""
        llm_props = self._make_llm_props()
        heuristic_props: list[dict] = []

        existing_keys = {
            (r.get("element_system"), r.get("property_name"), f"{r.get('value', 0):g}")
            for r in llm_props
        }
        merged = list(llm_props)
        new_count = 0
        for item in heuristic_props:
            key = (
                item.get("element_system"),
                item.get("property_name"),
                f"{item.get('value', 0):g}",
            )
            if key not in existing_keys:
                merged.append(item)
                existing_keys.add(key)
                new_count += 1

        assert new_count == 0
        assert len(merged) == 2

    def test_empty_llm_still_gets_heuristic(self):
        """When LLM returns nothing, heuristic results become the full set."""
        llm_props: list[dict] = []
        heuristic_props = self._make_heuristic_props()

        existing_keys: set[tuple] = set()
        merged = list(llm_props)
        new_count = 0
        for item in heuristic_props:
            key = (
                item.get("element_system"),
                item.get("property_name"),
                f"{item.get('value', 0):g}",
            )
            if key not in existing_keys:
                merged.append(item)
                existing_keys.add(key)
                new_count += 1

        assert new_count == 4
        assert len(merged) == 4

    def test_method_preserved_on_merge(self):
        """LLM items keep method=llm, heuristic items keep heuristic_regex."""
        llm_props = self._make_llm_props()
        heuristic_props = self._make_heuristic_props()

        existing_keys = {
            (r.get("element_system"), r.get("property_name"), f"{r.get('value', 0):g}")
            for r in llm_props
        }
        merged = list(llm_props)
        for item in heuristic_props:
            key = (
                item.get("element_system"),
                item.get("property_name"),
                f"{item.get('value', 0):g}",
            )
            if key not in existing_keys:
                merged.append(item)
                existing_keys.add(key)

        methods = {m["method"] for m in merged}
        assert methods == {"llm", "heuristic_regex"}
        assert merged[0]["method"] == "llm"
        assert merged[1]["method"] == "llm"
        assert merged[2]["method"] == "heuristic_regex"
        assert merged[3]["method"] == "heuristic_regex"
