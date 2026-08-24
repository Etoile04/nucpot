"""Backward-compatibility shim for ``property_catalog.STANDARD_PROPERTIES`` (NFM-3537).

The hardcoded alias mapping in ``nfm_db.core.property_catalog`` is being
phased out in favour of the ontology-driven loader
(``load_standard_properties``). Until every caller migrates, the legacy
attribute continues to resolve — but emits a ``DeprecationWarning`` so
tooling and humans can spot the stragglers.

These tests pin the shim contract:

* Accessing ``STANDARD_PROPERTIES`` through the module issues a
  ``DeprecationWarning`` whose message points at the ontology loader.
* The returned mapping still satisfies the legacy ``dict`` / ``len`` /
  ``.get`` / ``.keys`` / ``.values`` contract the rest of the codebase
  and the legacy unit tests rely on.
* ``load_standard_properties()`` returns the same shape and is itself
  warning-free (it is the canonical target).
"""

from __future__ import annotations

import importlib
import warnings

import pytest

_PROPERTY_CATALOG = "nfm_db.core.property_catalog"


@pytest.fixture
def property_catalog_module():
    """Re-import the module so the shim's warning fires from a stable location."""
    module = importlib.import_module(_PROPERTY_CATALOG)
    return importlib.reload(module)


class TestStandardPropertiesShim:
    """Behavioural contract for the deprecated ``STANDARD_PROPERTIES`` shim."""

    def test_access_emits_deprecation_warning(self, property_catalog_module) -> None:
        """Accessing ``STANDARD_PROPERTIES`` MUST raise ``DeprecationWarning``."""
        module = property_catalog_module
        with pytest.warns(DeprecationWarning, match=r"deprecated"):
            _ = module.STANDARD_PROPERTIES

    def test_warning_message_points_at_ontology_loader(self, property_catalog_module) -> None:
        """The warning message MUST mention the ontology loader as the migration target."""
        module = property_catalog_module
        with pytest.warns(DeprecationWarning) as record:
            _ = module.STANDARD_PROPERTIES
        message = str(record[0].message)
        assert "ontology" in message.lower(), (
            f"Warning must point users at the ontology loader migration target; got: {message!r}"
        )

    def test_returns_dict_like_aliases(self, property_catalog_module) -> None:
        """The shim MUST preserve the dict contract legacy callers rely on."""
        module = property_catalog_module
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            mapping = module.STANDARD_PROPERTIES
        assert isinstance(mapping, dict)
        assert len(mapping) >= 60
        # Spot-check a known alias preserved from the v4 catalog.
        assert mapping.get("density") == "密度"

    def test_unknown_attribute_raises_attribute_error(self, property_catalog_module) -> None:
        """Arbitrary attribute lookups MUST still raise ``AttributeError``."""
        module = property_catalog_module
        with pytest.raises(AttributeError, match="not_a_real_attr"):
            _ = module.not_a_real_attr


class TestLoadStandardProperties:
    """The canonical loader is itself warning-free and returns the same shape."""

    def test_load_standard_properties_is_warning_free(self) -> None:
        """``load_standard_properties`` is the migration target — no warning expected."""
        from nfm_db.core.property_catalog import load_standard_properties

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mapping = load_standard_properties()
        deprecation = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert deprecation == [], (
            "load_standard_properties() must not emit DeprecationWarning; got: "
            f"{[str(w.message) for w in deprecation]}"
        )
        assert isinstance(mapping, dict)
        assert len(mapping) >= 60
        assert mapping.get("density") == "密度"

    def test_module_does_not_define_standard_properties_at_top_level(self) -> None:
        """NFM-3537 strips the module-level literal so the shim is the only path.

        The ``__getattr__`` shim must own the name; the legacy ``STANDARD_PROPERTIES``
        module attribute must be absent from the module's ``__dict__`` so legacy
        callers can't bypass the warning by holding a reference captured at import
        time before the shim fires.
        """
        module = importlib.import_module(_PROPERTY_CATALOG)
        assert "STANDARD_PROPERTIES" not in vars(module), (
            "Module must not carry a module-level STANDARD_PROPERTIES attribute; "
            "the __getattr__ shim is the sole resolver."
        )
