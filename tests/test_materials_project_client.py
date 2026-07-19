"""Tests for Materials Project API client with caching.

All tests use mocked API responses — no real API calls.
TDD RED phase: these tests define the expected behavior of materials_project_client.py.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import FrozenInstanceError
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

from nfm_db.ml.materials_project_client import (
    DEFAULT_CUTOFF_ENERGY,
    DEFAULT_KPOINT_DENSITY,
    DEFAULT_CODE,
    MPEntry,
    SupplementaryRecord,
    batch_query,
    composition_cache_key,
    fetch_by_material_id,
    fetch_entries_by_composition,
    read_cache_entry,
    write_cache_entry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_mp_entry() -> MPEntry:
    """A typical Materials Project entry for a BCC U-Zr alloy."""
    return MPEntry(
        material_id="mp-1234",
        composition={"U": 70.0, "Zr": 30.0},
        formation_energy_per_atom=-3.25,
        formation_energy_uncertainty=0.05,
        lattice_constants={"a": 3.52, "b": 3.52, "c": 3.52},
        lattice_type="BCC",
        functional="PBE",
        band_gap=0.0,
        is_gap_direct=False,
        e_above_hull=0.01,
    )


@pytest.fixture
def cache_dir(tmp_path):
    """Provide a temporary cache directory, cleaned up after each test."""
    mp_cache = tmp_path / "mp"
    mp_cache.mkdir(parents=True, exist_ok=True)
    return str(mp_cache)


@pytest.fixture
def sample_composition() -> Dict[str, float]:
    return {"U": 70.0, "Zr": 30.0}


# ---------------------------------------------------------------------------
# 1. MPEntry dataclass tests
# ---------------------------------------------------------------------------


class TestMPEntry:
    """Tests for the MPEntry frozen dataclass."""

    def test_mp_entry_is_frozen(self, sample_mp_entry: MPEntry) -> None:
        """MPEntry should be immutable (frozen dataclass)."""
        with pytest.raises(FrozenInstanceError):
            sample_mp_entry.material_id = "mp-9999"  # type: ignore[misc]

    def test_mp_entry_fields(self, sample_mp_entry: MPEntry) -> None:
        """MPEntry should store all fields correctly."""
        assert sample_mp_entry.material_id == "mp-1234"
        assert sample_mp_entry.composition == {"U": 70.0, "Zr": 30.0}
        assert sample_mp_entry.formation_energy_per_atom == pytest.approx(-3.25)
        assert sample_mp_entry.formation_energy_uncertainty == pytest.approx(0.05)
        assert sample_mp_entry.lattice_constants == {"a": 3.52, "b": 3.52, "c": 3.52}
        assert sample_mp_entry.lattice_type == "BCC"
        assert sample_mp_entry.functional == "PBE"
        assert sample_mp_entry.band_gap == 0.0
        assert sample_mp_entry.is_gap_direct is False
        assert sample_mp_entry.e_above_hull == pytest.approx(0.01)

    def test_mp_entry_optional_fields_none(self) -> None:
        """MPEntry optional fields can be None."""
        entry = MPEntry(
            material_id="mp-5678",
            composition={"Fe": 50.0, "Cr": 50.0},
            formation_energy_per_atom=-2.0,
            formation_energy_uncertainty=None,
            lattice_constants={"a": 2.87},
            lattice_type=None,
            functional="PBE",
            band_gap=None,
            is_gap_direct=None,
            e_above_hull=None,
        )
        assert entry.formation_energy_uncertainty is None
        assert entry.lattice_type is None
        assert entry.band_gap is None
        assert entry.is_gap_direct is None
        assert entry.e_above_hull is None


# ---------------------------------------------------------------------------
# 2. SupplementaryRecord dataclass tests
# ---------------------------------------------------------------------------


class TestSupplementaryRecord:
    """Tests for the SupplementaryRecord frozen dataclass."""

    def test_supplementary_record_is_frozen(self, sample_mp_entry: MPEntry) -> None:
        """SupplementaryRecord should be immutable."""
        record = SupplementaryRecord(
            element_system="U-Zr",
            composition='{"U": 70.0, "Zr": 30.0}',
            phase="BCC",
            functional="PBE",
            formation_energy=-3.25,
            formation_energy_uncertainty=0.05,
            cohesive_energy=None,
            lattice_constant_a=3.52,
            lattice_constant_b=3.52,
            lattice_constant_c=3.52,
            lattice_distortion=0.035,
            source_id="SUPPL-MP-mp-1234",
            cutoff_energy=DEFAULT_CUTOFF_ENERGY,
            kpoint_density=DEFAULT_KPOINT_DENSITY,
            code=DEFAULT_CODE,
        )
        with pytest.raises(FrozenInstanceError):
            record.formation_energy = -99.0  # type: ignore[misc]

    def test_supplementary_record_matches_dft_export_spec(
        self, sample_mp_entry: MPEntry
    ) -> None:
        """SupplementaryRecord field names should match DFT export spec §3."""
        record = _make_supplementary_record(sample_mp_entry)

        # Required fields from spec §3.1
        assert hasattr(record, "element_system")
        assert hasattr(record, "composition")
        assert hasattr(record, "phase")
        # Required fields from spec §3.2
        assert hasattr(record, "functional")
        assert hasattr(record, "cutoff_energy")
        assert hasattr(record, "kpoint_density")
        assert hasattr(record, "code")
        # Required fields from spec §3.3
        assert hasattr(record, "formation_energy")
        assert hasattr(record, "lattice_constant_a")
        assert hasattr(record, "lattice_distortion")
        # Source field from spec §3.5
        assert hasattr(record, "source_id")

    def test_supplementary_record_source_id_format(
        self, sample_mp_entry: MPEntry
    ) -> None:
        """source_id should follow SUPPL-MP-{material_id} format."""
        record = _make_supplementary_record(sample_mp_entry)
        assert record.source_id == "SUPPL-MP-mp-1234"
        assert record.source_id.startswith("SUPPL-MP-")


# ---------------------------------------------------------------------------
# 3. Composition cache key tests
# ---------------------------------------------------------------------------


class TestCompositionCacheKey:
    """Tests for SHA256-based cache key generation."""

    def test_cache_key_is_deterministic(self, sample_composition) -> None:
        """Same composition should produce the same hash."""
        key1 = composition_cache_key(sample_composition)
        key2 = composition_cache_key(sample_composition)
        assert key1 == key2

    def test_cache_key_independent_of_dict_order(self) -> None:
        """Key should not depend on insertion order of the dict."""
        comp1 = {"U": 70.0, "Zr": 30.0}
        comp2 = {"Zr": 30.0, "U": 70.0}
        assert composition_cache_key(comp1) == composition_cache_key(comp2)

    def test_cache_key_differs_for_different_compositions(self) -> None:
        """Different compositions should produce different hashes."""
        key1 = composition_cache_key({"U": 70.0, "Zr": 30.0})
        key2 = composition_cache_key({"U": 50.0, "Zr": 50.0})
        assert key1 != key2

    def test_cache_key_is_sha256_hex(self, sample_composition) -> None:
        """Cache key should be a 64-character hex string (SHA256)."""
        key = composition_cache_key(sample_composition)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


# ---------------------------------------------------------------------------
# 4. Cache write/read cycle tests
# ---------------------------------------------------------------------------


class TestCacheWriteRead:
    """Tests for JSON file cache write and read."""

    def test_write_and_read_roundtrip(
        self, cache_dir: str, sample_mp_entry: MPEntry
    ) -> None:
        """Writing an entry to cache and reading it back should return the same data."""
        composition = {"U": 70.0, "Zr": 30.0}
        write_cache_entry(cache_dir, composition, [sample_mp_entry])

        result = read_cache_entry(cache_dir, composition)
        assert result is not None
        assert len(result) == 1
        entry = result[0]
        assert entry.material_id == "mp-1234"
        assert entry.formation_energy_per_atom == pytest.approx(-3.25)

    def test_cache_file_created_on_disk(
        self, cache_dir: str, sample_mp_entry: MPEntry
    ) -> None:
        """Cache write should create a JSON file in the cache directory."""
        composition = {"U": 70.0, "Zr": 30.0}
        write_cache_entry(cache_dir, composition, [sample_mp_entry])

        key = composition_cache_key(composition)
        cache_file = os.path.join(cache_dir, f"{key}.json")
        assert os.path.isfile(cache_file)

    def test_read_nonexistent_cache_returns_none(self, cache_dir: str) -> None:
        """Reading a cache entry that doesn't exist should return None."""
        composition = {"Pu": 50.0, "Am": 50.0}
        result = read_cache_entry(cache_dir, composition)
        assert result is None

    def test_cache_miss_different_composition(
        self, cache_dir: str, sample_mp_entry: MPEntry
    ) -> None:
        """Reading a different composition should return None (cache miss)."""
        written = {"U": 70.0, "Zr": 30.0}
        queried = {"U": 50.0, "Zr": 50.0}
        write_cache_entry(cache_dir, written, [sample_mp_entry])

        result = read_cache_entry(cache_dir, queried)
        assert result is None

    def test_cache_overwrite_replaces_data(
        self, cache_dir: str, sample_mp_entry: MPEntry
    ) -> None:
        """Writing twice to the same composition key should overwrite."""
        composition = {"U": 70.0, "Zr": 30.0}
        write_cache_entry(cache_dir, composition, [sample_mp_entry])

        updated_entry = MPEntry(
            material_id="mp-9999",
            composition={"U": 70.0, "Zr": 30.0},
            formation_energy_per_atom=-4.0,
            formation_energy_uncertainty=None,
            lattice_constants={"a": 3.60},
            lattice_type="BCC",
            functional="PBE",
            band_gap=None,
            is_gap_direct=None,
            e_above_hull=0.0,
        )
        write_cache_entry(cache_dir, composition, [updated_entry])

        result = read_cache_entry(cache_dir, composition)
        assert result is not None
        assert len(result) == 1
        assert result[0].material_id == "mp-9999"


# ---------------------------------------------------------------------------
# 5. fetch_entries_by_composition tests (mocked API)
# ---------------------------------------------------------------------------


class TestFetchEntriesByComposition:
    """Tests for the composition-based MP API query."""

    @patch.dict(os.environ, {"MP_API_KEY": "test-key-12345"})
    def test_returns_entries_for_matching_composition(
        self, sample_mp_entry: MPEntry
    ) -> None:
        """Should return a list of MPEntry objects for a matching composition."""
        mock_client = MagicMock()
        mock_summary = _make_mock_mp_summary(sample_mp_entry)
        mock_client.materials.summary.return_value = [mock_summary]

        with patch(
            "mp_api.client.MPRester", return_value=mock_client
        ):
            results = fetch_entries_by_composition(
                {"U": 70.0, "Zr": 30.0}, api_key="test-key-12345"
            )

        assert len(results) >= 1
        assert results[0].material_id == "mp-1234"
        assert results[0].formation_energy_per_atom == pytest.approx(-3.25)

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key_raises_error(self) -> None:
        """Should raise a clear error when API key is not provided."""
        with pytest.raises(ValueError, match="MP_API_KEY"):
            fetch_entries_by_composition({"U": 70.0, "Zr": 30.0}, api_key="")

    def test_empty_results_returns_empty_list(self) -> None:
        """Should return empty list when no entries match (not an error)."""
        mock_client = MagicMock()
        mock_client.materials.summary.return_value = []

        with patch(
            "mp_api.client.MPRester", return_value=mock_client
        ):
            results = fetch_entries_by_composition(
                {"Xx": 50.0, "Yy": 50.0}, api_key="test-key"
            )

        assert results == []


# ---------------------------------------------------------------------------
# 6. fetch_by_material_id tests (mocked API)
# ---------------------------------------------------------------------------


class TestFetchByMaterialId:
    """Tests for direct material ID lookup."""

    def test_returns_single_entry(self, sample_mp_entry: MPEntry) -> None:
        """Should return a single MPEntry for a valid material ID."""
        mock_entry_data = MagicMock()
        mock_entry_data.material_id = "mp-1234"
        mock_entry_data.formation_energy_per_atom = -3.25
        mock_entry_data.formation_energy_per_atom_uncertainty = 0.05
        mock_entry_data.structure.lattice.abc = [3.52, 3.52, 3.52]
        mock_entry_data.structure.lattice.type = "bcc"
        mock_entry_data.composition = {"U": 70.0, "Zr": 30.0}
        mock_entry_data.band_gap = 0.0
        mock_entry_data.e_above_hull = 0.01

        mock_client = MagicMock()
        mock_client.materials.get_entry_by_id.return_value = mock_entry_data

        with patch(
            "mp_api.client.MPRester", return_value=mock_client
        ):
            result = fetch_by_material_id("mp-1234", api_key="test-key")

        assert result.material_id == "mp-1234"
        assert result.formation_energy_per_atom == pytest.approx(-3.25)


# ---------------------------------------------------------------------------
# 7. batch_query tests (integration with caching)
# ---------------------------------------------------------------------------


class TestBatchQuery:
    """Tests for the batch query function with caching."""

    def test_batch_returns_supplementary_records(
        self, sample_mp_entry: MPEntry, cache_dir: str
    ) -> None:
        """batch_query should return SupplementaryRecord objects."""
        mock_client = MagicMock()
        mock_summary = _make_mock_mp_summary(sample_mp_entry)
        mock_client.materials.summary.return_value = [mock_summary]

        with patch(
            "mp_api.client.MPRester", return_value=mock_client
        ):
            results = batch_query(
                [{"U": 70.0, "Zr": 30.0}],
                api_key="test-key",
                cache_dir=cache_dir,
            )

        assert len(results) >= 1
        assert isinstance(results[0], SupplementaryRecord)
        assert results[0].source_id.startswith("SUPPL-MP-")

    def test_batch_cache_hit_avoids_api_call(
        self, cache_dir: str, sample_mp_entry: MPEntry
    ) -> None:
        """Second call for same composition should use cache, not API."""
        composition = {"U": 70.0, "Zr": 30.0}

        # Pre-populate cache
        write_cache_entry(cache_dir, composition, [sample_mp_entry])

        # API should NOT be called on cache hit
        mock_client = MagicMock()

        with patch(
            "mp_api.client.MPRester", return_value=mock_client
        ):
            results = batch_query(
                [composition], api_key="test-key", cache_dir=cache_dir
            )

        mock_client.materials.summary.assert_not_called()
        assert len(results) >= 1
        assert results[0].source_id == "SUPPL-MP-mp-1234"

    def test_batch_empty_list_returns_empty(self, cache_dir: str) -> None:
        """Empty input list should return empty results."""
        results = batch_query([], api_key="test-key", cache_dir=cache_dir)
        assert results == []

    @patch.dict(os.environ, {}, clear=True)
    def test_batch_missing_api_key_returns_empty(
        self, cache_dir: str
    ) -> None:
        """Missing API key should return empty list (graceful degradation)."""
        results = batch_query(
            [{"U": 70.0, "Zr": 30.0}],
            api_key="",
            cache_dir=cache_dir,
        )
        assert results == []


# ---------------------------------------------------------------------------
# 8. Rate limiting / backoff tests
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Tests for exponential backoff behavior."""

    def _make_429_error(self) -> Exception:
        """Create an exception that mimics a 429 HTTPError."""
        exc = Exception("429 Too Many Requests")
        exc.response = MagicMock(status_code=429)
        return exc

    @patch("nfm_db.ml.materials_project_client.time.sleep")
    def test_retry_on_rate_limit_error(self, mock_sleep: MagicMock) -> None:
        """Should retry with backoff when rate limited (429)."""
        error = self._make_429_error()
        mock_client = MagicMock()
        mock_client.materials.summary.side_effect = error

        with patch(
            "mp_api.client.MPRester", return_value=mock_client
        ):
            results = fetch_entries_by_composition(
                {"U": 70.0, "Zr": 30.0}, api_key="test-key"
            )

        # After retries, should return empty (graceful degradation)
        assert results == []
        # Should have called sleep for backoff (one fewer than retries)
        assert mock_sleep.call_count >= 1

    @patch("nfm_db.ml.materials_project_client.time.sleep")
    def test_backoff_delays_increase(self, mock_sleep: MagicMock) -> None:
        """Backoff delays should follow exponential pattern."""
        error = self._make_429_error()
        call_count = 0

        def side_effect_429(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise error

        mock_client = MagicMock()
        mock_client.materials.summary.side_effect = side_effect_429

        with patch(
            "mp_api.client.MPRester", return_value=mock_client
        ):
            fetch_entries_by_composition(
                {"U": 70.0, "Zr": 30.0}, api_key="test-key"
            )

        # Should have been called multiple times (retry behavior)
        assert call_count >= 2
        # Verify backoff delays are non-decreasing
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1]


# ---------------------------------------------------------------------------
# 9. MPEntry → SupplementaryRecord transformation tests
# ---------------------------------------------------------------------------


class TestTransformation:
    """Tests for converting MPEntry to SupplementaryRecord."""

    def test_transformation_preserves_composition(
        self, sample_mp_entry: MPEntry
    ) -> None:
        """Composition JSON in SupplementaryRecord should match MPEntry."""
        record = _make_supplementary_record(sample_mp_entry)
        parsed = json.loads(record.composition)
        assert parsed == sample_mp_entry.composition

    def test_transformation_uses_lattice_a(
        self, sample_mp_entry: MPEntry
    ) -> None:
        """lattice_constant_a should come from lattice_constants['a']."""
        record = _make_supplementary_record(sample_mp_entry)
        assert record.lattice_constant_a == pytest.approx(3.52)

    def test_transformation_defaults_for_missing_b_c(self) -> None:
        """lattice_constant_b and c should be None when not in lattice_constants."""
        entry = MPEntry(
            material_id="mp-tetragonal",
            composition={"U": 50.0, "Zr": 50.0},
            formation_energy_per_atom=-2.5,
            formation_energy_uncertainty=None,
            lattice_constants={"a": 3.50, "c": 5.80},
            lattice_type="BCT",
            functional="PBE",
            band_gap=None,
            is_gap_direct=None,
            e_above_hull=0.0,
        )
        record = _make_supplementary_record(entry)
        assert record.lattice_constant_a == pytest.approx(3.50)
        assert record.lattice_constant_b is None
        assert record.lattice_constant_c == pytest.approx(5.80)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_supplementary_record(entry: MPEntry) -> SupplementaryRecord:
    """Create a SupplementaryRecord from an MPEntry for testing."""
    from nfm_db.ml.materials_project_client import mp_entry_to_supplementary_record

    return mp_entry_to_supplementary_record(entry)


def _make_mock_mp_summary(entry: MPEntry) -> MagicMock:
    """Create a mock MP summary object matching _parse_summary_to_entries expectations."""
    mock = MagicMock()
    mock.material_id = entry.material_id
    mock.formation_energy_per_atom = entry.formation_energy_per_atom
    mock.formation_energy_per_atom_uncertainty = entry.formation_energy_uncertainty
    # composition_reduced is fractional (sums to 1.0), not at.%
    mock.composition_reduced = {el: frac / 100.0 for el, frac in entry.composition.items()}
    mock.structure = MagicMock()
    mock.structure.lattice = MagicMock()
    mock.structure.lattice.abc = list(entry.lattice_constants.values())
    mock.structure.lattice.type = (entry.lattice_type or "").lower()
    mock.band_gap = entry.band_gap
    mock.is_gap_direct = entry.is_gap_direct
    mock.e_above_hull = entry.e_above_hull
    return mock
