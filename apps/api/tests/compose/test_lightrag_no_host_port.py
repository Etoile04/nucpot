"""Static guard: prod/staging/preview LightRAG sidecar has no host port (NFM-4481).

Background
----------
NFM-4481 (LightRAG sidecar 9621 端口零认证) found that the LightRAG sidecar
was published on the host network with **zero authentication** on its query
API. ``docker compose up`` against ``docker-compose.prod.yml`` mapped
``9621:9621`` to ``0.0.0.0`` on the prod host, exposing ``/query`` and
``/query/stream`` to any LAN attacker. The approved fix (Option B in the
NFM-4481 plan) deletes every host-port mapping on the ``lightrag`` service
in the prod, staging, preview, and overlay compose files, and tightens the
dev-only mapping to ``127.0.0.1:9621:9621``.

What this test enforces
-----------------------
For each prod-shaped compose file (``docker-compose.prod.yml``,
``docker-compose.staging.yml``, ``docker-compose.preview.yml``, and the
``docker-compose.lightrag.yml`` overlay used in some preview/prod paths)
the ``lightrag`` service must NOT publish a host port. Concretely:

  * No ``ports:`` block at all on the ``lightrag`` service, OR
  * A ``ports:`` block whose entries contain neither ``9621`` nor
    ``0.0.0.0:``.

Why YAML and not regex
----------------------
Compose YAML supports list-of-mappings and list-of-strings for ``ports:``
both, plus env-var interpolation in either form. PyYAML gives a
single canonical view; regex is brittle to quoting and list-flattening.
The dev compose file (``docker/docker-compose.yml``) is checked separately
because its contract is different — LAN-blocked loopback binding only.

Failure modes
-------------
If a future change re-adds a host port mapping (e.g. someone copies a
prod compose from an older branch and forgets to strip the ``ports:``
block), this test fails on the matching compose file with the offending
block quoted verbatim. The dev-only contract is asserted by the sibling
test ``test_dev_compose_lightrag_is_loopback_only`` to keep the two
invariants independent.

Compose-specific YAML tags (``!override``, ``!reset``, ``!merge``)
-----------------------------------------------------------------
``docker-compose.preview.yml`` uses ``!override`` on the ``networks``
block (legitimate use of the compose spec). PyYAML's ``safe_load`` does
not understand those tags; the helpers below strip the tag prefix and
let the underlying mapping flow through ``safe_load`` unchanged. For a
static port-mapping check the wrapper is irrelevant (we never inspect
the override target itself).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Compose-spec custom tags that PyYAML's safe_load cannot resolve. Stripping
# the ``!foo`` prefix is a no-op for the mapping shape we care about.
_COMPOSE_TAG_RE = re.compile(r"!(?:override|reset|merge)\b")

# Repo-rooted absolute paths so the test is independent of CWD.
# Layout: <repo>/apps/api/tests/compose/test_lightrag_no_host_port.py
# parents[0] = tests/compose, [1] = tests, [2] = api, [3] = apps, [4] = repo
REPO_ROOT = Path(__file__).resolve().parents[4]

PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
PREVIEW_COMPOSE = REPO_ROOT / "docker-compose.preview.yml"
LIGHTRAG_OVERLAY_COMPOSE = REPO_ROOT / "docker-compose.lightrag.yml"
DEV_COMPOSE = REPO_ROOT / "docker" / "docker-compose.yml"

# Compose files where ``lightrag`` MUST have no host-port mapping. ``preview.yml``
# is included for symmetry because it composes on top of ``prod.yml`` and a
# future re-introduction of a ports block there would silently re-expose the
# sidecar to the host network. The overlay file is included because some
# preview / staging paths load it via ``-f docker-compose.lightrag.yml``.
PROD_SHAPED_COMPOSES: tuple[Path, ...] = (
    PROD_COMPOSE,
    STAGING_COMPOSE,
    PREVIEW_COMPOSE,
    LIGHTRAG_OVERLAY_COMPOSE,
)

# Substrings that, if present in any ``ports:`` mapping on the ``lightrag``
# service, mean the sidecar is reachable from outside the docker network.
_HOST_PORT_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = ("0.0.0.0:", "9621:")

# Dev-only contract: the dev compose is allowed to expose 9621, but only on
# the host loopback (``127.0.0.1``). LAN hosts MUST remain blocked.
_DEV_LOOPBACK_BINDING = "127.0.0.1:9621:9621"


def _load_yaml(path: Path) -> dict:
    """Load a compose YAML and return the top-level mapping.

    ``docker-compose.preview.yml`` uses the compose-specific ``!override``
    tag on its ``networks`` block; PyYAML's ``safe_load`` does not resolve
    it. We strip the tag prefix before parsing so the underlying mapping
    flows through unchanged. The ``services:`` block we inspect never uses
    custom tags, so this normalization is safe for the port-mapping check.

    We deliberately skip with a clear message if the file is missing so
    the test does not double-fail in stripped-down CI checkouts.
    """
    if not path.exists():
        pytest.skip(f"{path} not present in this checkout")
    text = path.read_text(encoding="utf-8")
    stripped = _COMPOSE_TAG_RE.sub("", text)
    loaded = yaml.safe_load(stripped)
    assert isinstance(loaded, dict), (
        f"{path}: top-level YAML must be a mapping (services:), got {type(loaded).__name__}"
    )
    return loaded


def _lightrag_service(path: Path) -> dict | None:
    """Return the ``lightrag`` service mapping, or ``None`` if absent.

    Differentiates between "the service is missing" (None) and "the service
    exists but has no ``ports:`` key" (a dict without ``ports``). The NFM-4481
    invariant is the second case — lightrag present, ports absent.
    """
    doc = _load_yaml(path)
    services = doc.get("services") or {}
    lightrag = services.get("lightrag")
    if lightrag is None:
        return None
    assert isinstance(lightrag, dict), (
        f"{path}: `services.lightrag` must be a mapping, got {type(lightrag).__name__}"
    )
    return lightrag


def _lightrag_ports(path: Path) -> list | None:
    """Return the ``ports:`` list for the ``lightrag`` service, or ``None``.

    Three-way return contract:
      * ``None`` — the ``lightrag`` service is missing entirely.
      * ``[]`` (empty list) — the service exists, ``ports:`` is absent or empty
        (the desired post-NFM-4481 state on prod-shaped compose).
      * ``[...]`` — non-empty ports list; the substring check applies.
    """
    lightrag = _lightrag_service(path)
    if lightrag is None:
        return None
    ports = lightrag.get("ports", [])
    if ports is None:
        return []
    assert isinstance(ports, list), (
        f"{path}: `services.lightrag.ports` must be a list, got {type(ports).__name__}"
    )
    return ports


def _normalize_port_mapping(entry: object) -> str:
    """Render any ``ports:`` entry as a ``HOST:CONTAINER`` string.

    Compose accepts both short-form strings (``"9621:9621"``) and long-form
    mappings (``{target: 9621, published: 9621}``). This helper flattens both
    into a canonical form so substring checks catch either shape.
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        # Long-form. ``host_ip`` defaults to ``0.0.0.0`` when omitted; we
        # mirror that here so the substring check matches compose runtime.
        host_ip = entry.get("host_ip", "0.0.0.0")
        published = entry.get("published", "")
        target = entry.get("target", "")
        if published and target:
            return f"{host_ip}:{published}:{target}"
        if target:
            return f"{host_ip}:{target}"
        return f"{host_ip}:{published}"
    return repr(entry)


# ---------------------------------------------------------------------------
# Prod / staging / preview / overlay — must NOT publish 9621 / 0.0.0.0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "compose_path",
    PROD_SHAPED_COMPOSES,
    ids=lambda p: p.name,
)
class TestNoHostPortOnLightrag:
    """Each prod-shaped compose file must keep the LightRAG sidecar off-host."""

    def test_lightrag_service_present(self, compose_path: Path) -> None:
        """The compose file declares a ``lightrag`` service (sanity)."""
        if _lightrag_service(compose_path) is None:
            # preview.yml composes on prod + uses ``--no-deps`` to skip the
            # sidecar, so its top-level service may legitimately be absent.
            if compose_path == PREVIEW_COMPOSE:
                pytest.skip(
                    "docker-compose.preview.yml does not declare a `lightrag` "
                    "service — the sidecar is started by prod compose only."
                )
            pytest.fail(
                f"{compose_path}: `services.lightrag` is missing entirely. "
                f"NFM-4481 requires the sidecar to remain declared (without "
                f"a host-port mapping) so prod / staging stacks still get it."
            )

    def test_no_host_port_mapping(self, compose_path: Path) -> None:
        """No ``ports:`` entry may contain ``0.0.0.0:`` or ``9621:``."""
        ports = _lightrag_ports(compose_path)
        if ports is None:
            pytest.skip(
                f"{compose_path}: no `lightrag` service declared — covered by "
                f"test_lightrag_service_present."
            )
        # The desired state: ``lightrag`` exists with no ``ports:`` block at
        # all (or an empty list). Anything else is a regression.
        offenders: list[str] = []
        for raw in ports:
            normalized = _normalize_port_mapping(raw)
            for needle in _HOST_PORT_FORBIDDEN_SUBSTRINGS:
                if needle in normalized:
                    offenders.append(f"{raw!r} -> {normalized!r} matches {needle!r}")
        assert not offenders, (
            f"{compose_path}: NFM-4481 forbids publishing the LightRAG "
            f"sidecar on a host port. Found forbidden mapping(s):\n  "
            + "\n  ".join(offenders)
            + "\nRemove the entire `ports:` block on the `lightrag` service."
        )


# ---------------------------------------------------------------------------
# Dev-only contract: loopback binding only
# ---------------------------------------------------------------------------


class TestDevComposeLightragIsLoopbackOnly:
    """The dev compose may expose 9621, but only on the host loopback.

    NFM-4481 narrowed ``docker/docker-compose.yml``'s ``lightrag`` mapping
    from ``9621:9621`` (which compose binds to ``0.0.0.0``) to
    ``127.0.0.1:9621:9621`` so LAN hosts cannot reach the sidecar from a
    shared office or coffee-shop network.
    """

    def test_lightrag_published_on_loopback_only(self) -> None:
        ports = _lightrag_ports(DEV_COMPOSE)
        if ports is None:
            pytest.skip(f"{DEV_COMPOSE}: no `lightrag` service — nothing to assert.")
        if not ports:
            pytest.skip(
                f"{DEV_COMPOSE}: `lightrag` has no `ports:` block — dev "
                f"contract is loopback binding; this compose apparently "
                f"removed the dev-only mapping. Update this test if that "
                f"is intentional."
            )
        rendered = [_normalize_port_mapping(p) for p in ports]
        assert _DEV_LOOPBACK_BINDING in rendered, (
            f"{DEV_COMPOSE}: expected `{_DEV_LOOPBACK_BINDING}` in "
            f"`services.lightrag.ports` (NFM-4481 dev-only loopback "
            f"contract), got {rendered!r}."
        )
        for raw, norm in zip(ports, rendered, strict=False):
            # Every mapping must start with ``127.0.0.1:`` — anything else
            # would either drop the loopback guard (``9621:9621``) or rebind
            # to a LAN-reachable address.
            if not norm.startswith("127.0.0.1:"):
                pytest.fail(
                    f"{DEV_COMPOSE}: lightrag port mapping {raw!r} -> {norm!r} "
                    f"is not loopback-only. NFM-4481 requires every entry to "
                    f"start with `127.0.0.1:` so LAN hosts cannot reach the "
                    f"sidecar."
                )

    def test_dev_compose_does_not_publish_to_all_interfaces(self) -> None:
        """Defensive: no entry may include ``0.0.0.0:`` (the old default)."""
        ports = _lightrag_ports(DEV_COMPOSE)
        if not ports:
            pytest.skip(f"{DEV_COMPOSE}: no `lightrag` ports entries.")
        for raw in ports:
            norm = _normalize_port_mapping(raw)
            assert "0.0.0.0:" not in norm, (
                f"{DEV_COMPOSE}: lightrag port mapping {raw!r} binds to "
                f"0.0.0.0, which is the NFM-4481 anti-pattern. Use "
                f"`127.0.0.1:9621:9621` instead."
            )


# ---------------------------------------------------------------------------
# Defensive: missing file / empty file / unknown shape / tag-stripping
# ---------------------------------------------------------------------------


class TestDefensiveLoadHelpers:
    """Cover the loader's defensive branches so a future regression does not
    silently change error semantics (NFM-4481 review checklist).
    """

    def test_missing_compose_file_is_skipped(self, tmp_path: Path) -> None:
        """A non-existent path short-circuits with ``pytest.skip``."""
        with pytest.raises(pytest.skip.Exception):
            _load_yaml(tmp_path / "does-not-exist.yml")

    def test_empty_compose_file_loads_to_none(self, tmp_path: Path) -> None:
        """An empty YAML file parses to ``None`` — surfaced as a hard fail.

        Compose files are never empty in practice; if one ever is, the test
        surfaces the malformed input instead of silently passing.
        """
        empty = tmp_path / "empty.yml"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(AssertionError, match="top-level YAML must be a mapping"):
            _load_yaml(empty)

    def test_top_level_non_mapping_is_rejected(self, tmp_path: Path) -> None:
        """A YAML whose top-level is a list (not a mapping) fails loudly."""
        bad = tmp_path / "list-root.yml"
        bad.write_text("- one\n- two\n", encoding="utf-8")
        with pytest.raises(AssertionError, match="top-level YAML must be a mapping"):
            _load_yaml(bad)

    def test_tag_stripping_handles_override_on_mapping(self, tmp_path: Path) -> None:
        """``!override`` wrapping a mapping survives safe_load via tag strip."""
        tagged = tmp_path / "tagged.yml"
        tagged.write_text(
            "networks:\n  prod: !override\n    external: true\n    name: x\n",
            encoding="utf-8",
        )
        doc = _load_yaml(tagged)
        # Without tag stripping, ``yaml.safe_load`` would raise ConstructorError.
        assert doc == {"networks": {"prod": {"external": True, "name": "x"}}}

    def test_dev_compose_with_no_ports_block_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A dev compose that drops its ``ports:`` block entirely is allowed.

        The NFM-4481 dev contract says "loopback binding only"; if a future
        change removes the dev port mapping altogether (e.g. switches to an
        ``expose:``-only setup), the loopback test should skip rather than
        fail, with a hint that the test needs updating.
        """
        empty_dev = tmp_path / "dev.yml"
        empty_dev.write_text(
            "services:\n  lightrag:\n    image: foo:1\n",
            encoding="utf-8",
        )
        # Re-target the dev-compose constant by writing to the real path's
        # directory then pointing the test at the synthetic file. Easier:
        # just exercise ``_lightrag_ports`` directly.
        lightrag = _lightrag_service(empty_dev)
        assert lightrag is not None
        assert _lightrag_ports(empty_dev) == []  # no ports key => []

    def test_long_form_port_with_explicit_host_ip(self) -> None:
        """Long-form ``ports:`` with ``host_ip`` is normalized to ``IP:PUB:TGT``."""
        norm = _normalize_port_mapping({"target": 9621, "published": 9621, "host_ip": "0.0.0.0"})
        assert norm == "0.0.0.0:9621:9621"
        assert "0.0.0.0:" in norm  # would be flagged by the host-port check

    def test_long_form_port_with_target_only(self) -> None:
        """Long-form with only ``target`` uses default ``0.0.0.0:target``."""
        norm = _normalize_port_mapping({"target": 9621})
        assert norm == "0.0.0.0:9621"
        assert "0.0.0.0:" in norm

    def test_long_form_port_with_published_only(self) -> None:
        """Long-form with only ``published`` uses ``0.0.0.0:published``."""
        norm = _normalize_port_mapping({"published": 9621})
        assert norm == "0.0.0.0:9621"

    def test_unrecognized_port_entry_renders_as_repr(self) -> None:
        """Non-string, non-dict entries fall through to ``repr()`` (defensive)."""
        norm = _normalize_port_mapping(12345)
        assert norm == "12345"


# ---------------------------------------------------------------------------
# Regression fixture: prove the test CATCHES a forbidden mapping
# ---------------------------------------------------------------------------


class TestRegressionFixtures:
    """Inject a bad compose into a tmp_path and confirm the test fires.

    These run only against synthetic in-memory files; the real prod-shaped
    compose files are left untouched. They exist so a future refactor that
    silently weakens the substring check (e.g. by switching to a permissive
    regex) is caught by CI before it lands.
    """

    @pytest.fixture
    def bad_prod_compose(self, tmp_path: Path) -> Path:
        """Synthetic prod compose with a re-introduced host port mapping."""
        path = tmp_path / "docker-compose.prod.yml"
        path.write_text(
            "services:\n"
            "  lightrag:\n"
            '    image: "nucpot-prod-lightrag:1"\n'
            "    ports:\n"
            '      - "9621:9621"\n',
            encoding="utf-8",
        )
        return path

    @pytest.fixture
    def loopback_only_dev_compose(self, tmp_path: Path) -> Path:
        """Synthetic dev compose with the correct loopback binding."""
        path = tmp_path / "docker-compose.yml"
        port_line = '      - "127.0.0.1:9621:9621"\n'
        body = (
            'services:\n  lightrag:\n    image: "nucpot-dev-lightrag:1"\n    ports:\n' + port_line
        )
        path.write_text(body, encoding="utf-8")
        return path

    def test_bad_prod_compose_is_rejected(self, bad_prod_compose: Path) -> None:
        """A re-introduced ``9621:9621`` on ``lightrag`` fails the assertion."""
        ports = _lightrag_ports(bad_prod_compose)
        assert ports is not None and ports, (
            "fixture must declare a non-empty `ports:` list to be a valid regression case"
        )
        offenders = [
            raw
            for raw in ports
            for needle in _HOST_PORT_FORBIDDEN_SUBSTRINGS
            if needle in _normalize_port_mapping(raw)
        ]
        assert offenders, (
            "regression fixture should produce at least one offender "
            f"(forbidden substrings: {_HOST_PORT_FORBIDDEN_SUBSTRINGS!r}); "
            f"got normalized mappings: "
            f"{[_normalize_port_mapping(p) for p in ports]!r}"
        )

    def test_loopback_dev_compose_passes(self, loopback_only_dev_compose: Path) -> None:
        """The loopback-only dev compose is accepted by both helpers."""
        ports = _lightrag_ports(loopback_only_dev_compose)
        assert ports == ["127.0.0.1:9621:9621"]
        rendered = [_normalize_port_mapping(p) for p in ports]
        assert all(r.startswith("127.0.0.1:") for r in rendered)
        assert all("0.0.0.0:" not in r for r in rendered)
