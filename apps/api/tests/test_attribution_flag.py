"""Unit tests for the NFM-4159 attribution-flag module.

Pins §5.1 / option (a) config-driven behaviour:
  * Comma-separated UUID list parsed once at module load.
  * Empty env value / unset env → empty tuple (safe no-op).
  * Invalid UUID entries are rejected loudly (CTO must supply valid UUIDs).
  * Memoisation survives subsequent mutations of ``os.environ`` inside the
    same process (consumers can reset via the private reset helper).
  * Whitespace tolerance around commas is honoured.
"""

from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(autouse=True)
def _reset_attribution_flag_cache() -> None:
    """Drop memoised cache + clear env between tests."""
    from nfm_db.services import attribution_flag

    attribution_flag.reset_attribution_flag_cache()
    old = os.environ.pop(attribution_flag.ATTRIBUTION_LOST_CANONICAL_ENV, None)
    try:
        yield
    finally:
        attribution_flag.reset_attribution_flag_cache()
        if old is not None:
            os.environ[attribution_flag.ATTRIBUTION_LOST_CANONICAL_ENV] = old


def test_attribution_flag_unset_returns_empty_tuple() -> None:
    from nfm_db.services.attribution_flag import (
        get_attribution_lost_at,
        get_lost_canonical_data_source_ids,
    )

    # Env var is unset (fixture cleaned it).  Default must be EMPTY.
    assert get_lost_canonical_data_source_ids() == ()
    # Lost-at stamp is the locked contract value per §5.2.
    assert get_attribution_lost_at().isoformat() == "2026-09-02"


def test_attribution_flag_empty_string_returns_empty_tuple() -> None:
    from nfm_db.services.attribution_flag import get_lost_canonical_data_source_ids

    os.environ["NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"] = ""
    assert get_lost_canonical_data_source_ids() == ()


def test_attribution_flag_parses_comma_separated_uuids() -> None:
    from nfm_db.services.attribution_flag import get_lost_canonical_data_source_ids

    a, b, c, d = (str(uuid.uuid4()) for _ in range(4))
    os.environ["NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"] = f"{a},{b},{c},{d}"
    ids = get_lost_canonical_data_source_ids()
    assert len(ids) == 4
    assert set(ids) == {uuid.UUID(a), uuid.UUID(b), uuid.UUID(c), uuid.UUID(d)}


def test_attribution_flag_tolerates_whitespace_around_commas() -> None:
    from nfm_db.services.attribution_flag import get_lost_canonical_data_source_ids

    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    os.environ["NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"] = f"  {a} , {b}  "
    ids = get_lost_canonical_data_source_ids()
    assert ids == (uuid.UUID(a), uuid.UUID(b))


def test_attribution_flag_rejects_invalid_uuid() -> None:
    from nfm_db.services import attribution_flag
    from nfm_db.services.attribution_flag import get_lost_canonical_data_source_ids

    os.environ["NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"] = "not-a-uuid"
    with pytest.raises(ValueError, match="not-a-uuid"):
        get_lost_canonical_data_source_ids()
    # Cache should NOT be poisoned on bad parse.
    attribution_flag.reset_attribution_flag_cache()
    os.environ["NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"] = ""
    assert get_lost_canonical_data_source_ids() == ()


def test_attribution_flag_memoises_after_first_read() -> None:
    """Once read, the cached value must NOT silently change on env mutation.

    Tests can reset the cache via ``reset_attribution_flag_cache``.
    """
    from nfm_db.services import attribution_flag
    from nfm_db.services.attribution_flag import get_lost_canonical_data_source_ids

    a = str(uuid.uuid4())
    os.environ["NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"] = a
    assert get_lost_canonical_data_source_ids() == (uuid.UUID(a),)

    # Mutate the environment AFTER first read. The cached value must persist.
    os.environ["NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"] = str(uuid.uuid4())
    assert get_lost_canonical_data_source_ids() == (uuid.UUID(a),)

    # After explicit reset, the new env value is honoured.
    attribution_flag.reset_attribution_flag_cache()
    new_ids = get_lost_canonical_data_source_ids()
    assert new_ids != (uuid.UUID(a),)


def test_attribution_flag_feature_flag_name_constant() -> None:
    """The constant must round-trip through the helper (used in audit trail)."""
    from nfm_db.services.attribution_flag import (
        ATTRIBUTION_FEATURE_FLAG_NAME,
        attribution_feature_flag_name,
    )

    assert attribution_feature_flag_name() == ATTRIBUTION_FEATURE_FLAG_NAME
    assert ATTRIBUTION_FEATURE_FLAG_NAME.startswith("ATTRIBUTION_")
