"""Skills engine factory + pin-lock wiring for path-A extraction (NFM-4547).

Implements ADR-016 §2.7 + G1 spec §8.1: the production path-A extractor
(`ontofuel_extract`) can opt-in to a skill-driven prompt instead of the
default ontology-driven prompt. The skill body lives in an external
repository pinned via ``EXTRACTION_SKILL_REPO_PIN`` (git SHA), with a
human-readable version stamp in ``EXTRACTION_SKILL_VERSION``.

Rollout posture (§4.1 of ADR-016, "dark-then-light"):

* ``EXTRACTION_SKILL_ENABLED`` defaults to ``False`` — the legacy ontology
  prompt is used. Flip to ``True`` only after the Beeler 2018 recall
  regression passes for the pinned version (§2.8).
* ``EXTRACTION_SKILL_REPO_PIN`` is mandatory when the flag is on. If the
  env var disagrees with ``packages/skills-catalog/lock.yaml``, the
  factory raises ``SkillPinMismatchError`` so an unexpected config never
  silently slips through.
* CI runs ``scripts/check_skill_pin.py`` to fail closed when the env var
  drifts away from the lock file — ``AC-8``.

Public API:
    extract_skill_prompt(skill_version, ontology_version) -> str
    load_lock_file() -> SkillsLock
    resolve_skill_pin() -> tuple[str, str]   # (version, sha); strict when the
    # flag is on, lock fallback when off (NFM-4626)
    SkillPinMismatchError
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from nfm_db.services.extraction_prompt import build_ontology_extraction_prompt

if TYPE_CHECKING:  # pragma: no cover
    from nfm_db.models.ontology_version import OntologyVersion

__all__ = [
    "EXTRACTION_SKILL_ENABLED_ENV",
    "EXTRACTION_SKILL_REPO_PIN_ENV",
    "EXTRACTION_SKILL_VERSION_ENV",
    "SkillPinMismatchError",
    "SkillsLock",
    "extract_skill_prompt",
    "is_skill_enabled",
    "load_lock_file",
    "resolve_skill_pin",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env contract
# ---------------------------------------------------------------------------

#: Toggle for the dark-then-light rollout (§4.1 of ADR-016). Default = off so
#: production starts with the ontology prompt; on only after Beeler ≥4/4.
EXTRACTION_SKILL_ENABLED_ENV: str = "EXTRACTION_SKILL_ENABLED"

#: Upstream skill repo pin (full git SHA, 40 hex chars). Required when the
#: flag is on. CI must keep this in sync with
#: ``packages/skills-catalog/lock.yaml`` (AC-8).
EXTRACTION_SKILL_REPO_PIN_ENV: str = "EXTRACTION_SKILL_REPO_PIN"

#: Human-readable semantic version stamp (e.g. ``v1.7.2``). Surfaced in
#: ``property_measurements.extraction_skill_version`` for audit.
EXTRACTION_SKILL_VERSION_ENV: str = "EXTRACTION_SKILL_VERSION"


# Repo-root-relative path to the lock file. The factory looks it up by
# walking upward from this file (works in dev + container + CI without
# any env plumbing).
_LOCK_FILE_REPO_RELATIVE: str = "packages/skills-catalog/lock.yaml"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SkillPinMismatchError(RuntimeError):
    """Raised when EXTRACTION_SKILL_REPO_PIN disagrees with the lock file.

    AC-8 / ADR-016 §2.7. Fails closed — never silently coerce.
    """

    def __init__(
        self,
        *,
        env_pin: str | None,
        lock_pin: str | None,
        env_version: str | None,
        lock_version: str | None,
        message: str | None = None,
    ) -> None:
        self.env_pin = env_pin
        self.lock_pin = lock_pin
        self.env_version = env_version
        self.lock_version = lock_version
        if message is not None:
            super().__init__(message)
        else:
            super().__init__(
                "EXTRACTION_SKILL_REPO_PIN disagrees with packages/skills-catalog/lock.yaml: "
                f"env={env_pin!r} vs lock={lock_pin!r}; "
                f"env_version={env_version!r} vs lock_version={lock_version!r}. "
                "Resolve the drift (PR or env update) — the platform fails closed."
            )


# ---------------------------------------------------------------------------
# Lock file loader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillsLock:
    """Typed view of ``packages/skills-catalog/lock.yaml``.

    Only the fields the factory needs at runtime are exposed. Other keys
    pass through as the original mapping for richer audit logs.
    """

    catalog_id: str
    upstream_url: str
    pin: str
    ref: str | None
    default_skill: str
    skills: dict[str, dict[str, Any]]
    raw: dict[str, Any]


def _resolve_lock_file_path() -> Path:
    """Locate the lock file by walking up from this module's directory."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / _LOCK_FILE_REPO_RELATIVE
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Skills catalog lock file not found via {_LOCK_FILE_REPO_RELATIVE}; "
        f"walked up from {here}"
    )


def load_lock_file(path: Path | str | None = None) -> SkillsLock:
    """Load and parse the skills catalog lock file.

    Args:
        path: Optional override (used by the CI guard). Defaults to the
            repo-relative ``packages/skills-catalog/lock.yaml``.

    Raises:
        FileNotFoundError: When the file cannot be located.
        ValueError: When the YAML is missing required keys.
    """
    lock_path = Path(path) if path else _resolve_lock_file_path()
    raw = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Lock file root must be a mapping; got {type(raw).__name__}")

    catalog = raw.get("catalog") or {}
    upstream = catalog.get("upstream") or {}
    skills = raw.get("skills") or {}

    required = (
        ("catalog.id", catalog.get("id")),
        ("catalog.upstream.url", upstream.get("url")),
        ("catalog.upstream.pin", upstream.get("pin")),
        ("catalog.default_skill", catalog.get("default_skill")),
    )
    missing = [k for k, v in required if not v]
    if missing:
        raise ValueError(
            f"Lock file {lock_path} missing required keys: {', '.join(missing)}"
        )

    return SkillsLock(
        catalog_id=str(catalog["id"]),
        upstream_url=str(upstream["url"]),
        pin=str(upstream["pin"]),
        ref=upstream.get("ref"),
        default_skill=str(catalog["default_skill"]),
        skills={k: dict(v) for k, v in skills.items()},
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Env / pin resolution
# ---------------------------------------------------------------------------


_FLAG_TRUTHY_VALUES: tuple[str, ...] = ("1", "true", "yes", "on")


def _flag_is_on(raw: str | None) -> bool:
    """Truthiness test shared by every reader of ``EXTRACTION_SKILL_ENABLED``."""
    return (raw or "").strip().lower() in _FLAG_TRUTHY_VALUES


def is_skill_enabled() -> bool:
    """Read ``EXTRACTION_SKILL_ENABLED``. Default ``False`` (dark launch)."""
    return _flag_is_on(os.environ.get(EXTRACTION_SKILL_ENABLED_ENV))


def resolve_skill_pin(
    *,
    env: dict[str, str] | None = None,
    lock: SkillsLock | None = None,
) -> tuple[str, str]:
    """Return ``(version, sha)`` after cross-checking env ↔ lock file.

    NFM-4626: when ``EXTRACTION_SKILL_ENABLED`` is on this is the strict
    variant — it delegates to ``assert_pin_consistent`` so a missing
    ``EXTRACTION_SKILL_REPO_PIN`` (or a zero-placeholder lock pin)
    raises instead of resolving. When the flag is off (the dark-launch
    default) it returns the env values when present and falls back to
    the lock file values when env is unset (e.g. in tests where the lock
    file is the source of truth) — that fallback is load-bearing
    (NFM-4618 AC3) and must not tighten.
    """
    env_map = env if env is not None else os.environ
    lock = lock or load_lock_file()

    # NFM-4626: read the flag from env_map (not os.environ) so the
    # function stays self-contained for testing — mirrors the NFM-4618
    # fix in ``assert_pin_consistent``.
    flag_on = _flag_is_on(env_map.get(EXTRACTION_SKILL_ENABLED_ENV))

    if flag_on:
        # NFM-4626 (Option 1): the lenient fallback must never apply to
        # an enabled skill path — delegate to the strict twin so the
        # two resolvers cannot diverge again (the exact hole NFM-4618
        # closed in ``assert_pin_consistent`` only).
        return assert_pin_consistent(env=env, lock=lock)

    env_version = env_map.get(EXTRACTION_SKILL_VERSION_ENV)
    env_pin = env_map.get(EXTRACTION_SKILL_REPO_PIN_ENV)

    default_skill = lock.skills.get(lock.default_skill) or {}
    lock_version = str(default_skill.get("version", ""))
    lock_pin = lock.pin

    version = env_version or lock_version
    sha = env_pin or lock_pin
    return version, sha


def assert_pin_consistent(
    *,
    env: dict[str, str] | None = None,
    lock: SkillsLock | None = None,
) -> tuple[str, str]:
    """Strict pin check — raises ``SkillPinMismatchError`` on any drift.

    Used by both the factory and the CI guard.
    """
    env_map = env if env is not None else os.environ
    lock = lock or load_lock_file()

    env_version = env_map.get(EXTRACTION_SKILL_VERSION_ENV)
    env_pin = env_map.get(EXTRACTION_SKILL_REPO_PIN_ENV)

    # NFM-4618: read the flag from env_map too (not os.environ) so the
    # whole function stays self-contained for testing. Mirrors the
    # NFM-4611 fix in the CI guard: the flag is part of the strict
    # contract, not an ambient env read.
    flag_on = _flag_is_on(env_map.get(EXTRACTION_SKILL_ENABLED_ENV))

    default_skill = lock.skills.get(lock.default_skill) or {}
    lock_version = str(default_skill.get("version", ""))
    lock_pin = lock.pin

    # NFM-4618: the pin is mandatory whenever the flag is on. Without
    # this check, ``EXTRACTION_SKILL_ENABLED=true`` with no
    # ``EXTRACTION_SKILL_REPO_PIN`` would fall through and
    # ``resolve_skill_pin`` would return the lock value — which today
    # is the 40-zero placeholder. The CI guard catches the same
    # configuration pre-deploy; this mirrors it for any path that
    # bypasses CI (manual container run, direct env edit, test harness).
    if flag_on and not env_pin:
        raise SkillPinMismatchError(
            env_pin=env_pin or "",
            lock_pin=lock_pin,
            env_version=env_version,
            lock_version=lock_version,
            message=(
                "EXTRACTION_SKILL_ENABLED is on but EXTRACTION_SKILL_REPO_PIN "
                "is unset; the pin is mandatory whenever the skill path is enabled"
            ),
        )

    # When env sets the flag we require both version AND pin to be set,
    # and to match the lock. A zero SHA in the lock is treated as
    # "uninitialised" — never used at runtime.
    # NFM-4618: was ``if env_pin and ...``. A zero-placeholder lock pin
    # is never runnable once the flag flips, whether or not the caller
    # supplied env_pin.
    if (env_pin or flag_on) and lock_pin.startswith("0" * 40):
        raise SkillPinMismatchError(
            env_pin=env_pin,
            lock_pin=lock_pin,
            env_version=env_version,
            lock_version=lock_version,
            message=(
                "lock file upstream.pin is the zero placeholder; "
                "set a real SHA before flipping EXTRACTION_SKILL_ENABLED=true"
            ),
        )

    if env_pin and env_pin != lock_pin:
        raise SkillPinMismatchError(
            env_pin=env_pin,
            lock_pin=lock_pin,
            env_version=env_version,
            lock_version=lock_version,
        )

    if env_version and lock_version and env_version != lock_version:
        raise SkillPinMismatchError(
            env_pin=env_pin,
            lock_pin=lock_pin,
            env_version=env_version,
            lock_version=lock_version,
        )

    return (env_version or lock_version), (env_pin or lock_pin)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _resolve_locked_entrypoint_path(lock: SkillsLock, skill_id: str) -> Path | None:
    """Resolve the on-disk SKILL.md path for *skill_id* under the platform.

    The platform does NOT vendor upstream skill content. For local dev /
    CI dry-runs we honour ``packages/skills-catalog/skills/<id>/SKILL.md``
    when present; in production this directory is intentionally empty
    because the prompt is fetched at runtime from the pinned upstream
    SHA. When the file is absent the factory returns ``None`` and the
    caller falls back to the ontology prompt with a clear log line —
    keeping the seam testable without external network.
    """
    skill_meta = lock.skills.get(skill_id) or {}
    rel = skill_meta.get("entrypoint")
    if not rel:
        return None
    lock_path = _resolve_lock_file_path()
    candidate = lock_path.parent / "skills" / Path(rel).name
    return candidate if candidate.is_file() else None


def extract_skill_prompt(
    skill_version: str | None,
    ontology_version: OntologyVersion,
    *,
    lock: SkillsLock | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Build the extraction system prompt for the requested skill.

    Behaviour:

    * When ``EXTRACTION_SKILL_ENABLED`` is falsy (default) → return the
      legacy ontology-driven prompt unchanged. This is the dark-launch
      path; no behaviour change is shipped until AC-1 (Beeler ≥4/4) is
      green.
    * When the flag is on → cross-check env ↔ lock pin, then either load
      ``packages/skills-catalog/skills/<id>/SKILL.md`` (local dry-run)
      or fall back to the ontology prompt with a structured warning
      so an empty upstream clone never silently degrades to a stale
      contract.

    Args:
        skill_version: The skill id (e.g. ``"nuclear-property-extraction-v4"``).
            ``None`` means "use the lock file's default".
        ontology_version: The published ``OntologyVersion`` used to build
            the ontology context block. The skill prompt re-uses it for
            the categories / standard-names section so the LLM still
            sees the canonical 11-category enumeration.
        lock: Pre-loaded ``SkillsLock`` (mainly for tests).
        env: Override env map (mainly for tests).

    Raises:
        SkillPinMismatchError: When env pin disagrees with the lock file
            (``AC-8``).
    """
    lock = lock or load_lock_file()
    skill_id = skill_version or lock.default_skill

    if not is_skill_enabled():
        logger.debug(
            "extract_skill_prompt: skill flag off — using ontology prompt "
            "(skill_id=%s)",
            skill_id,
        )
        return build_ontology_extraction_prompt(ontology_version)

    # Flag on → strict pin check.
    env_version, env_pin = assert_pin_consistent(env=env, lock=lock)
    logger.info(
        "extract_skill_prompt: skill ON — skill_id=%s version=%s pin=%s",
        skill_id,
        env_version,
        env_pin,
    )

    skill_meta = lock.skills.get(skill_id) or {}
    upstream_entry = skill_meta.get("entrypoint") or ""

    local_path = _resolve_locked_entrypoint_path(lock, skill_id)
    if local_path is None:
        # Production posture: platform does not vendor upstream content.
        # Logged warning + fall back to ontology prompt keeps the seam
        # functional but makes the missing-pin obvious in observability.
        logger.warning(
            "extract_skill_prompt: local SKILL.md not present for skill_id=%s "
            "(expected %s). Falling back to ontology prompt — verify upstream "
            "clone of pin=%s is reachable at runtime.",
            skill_id,
            upstream_entry,
            env_pin,
        )
        return build_ontology_extraction_prompt(ontology_version)

    skill_body = local_path.read_text(encoding="utf-8")

    # Compose: upstream SKILL.md + ontology-derived categories / standard
    # names so the LLM still gets the canonical enumeration. The
    # ontology helper is intentionally imported lazily inside the
    # function so unit tests can call the factory without a DB session.
    ontology_data = ontology_version.ontology_data or {}
    from nfm_db.services.extraction_prompt import (
        _build_ontology_categories_block,
        _build_ontology_standard_names_block,
    )

    categories_block = _build_ontology_categories_block(ontology_data)
    standard_names_block = _build_ontology_standard_names_block(ontology_data)

    skill_header = (
        "# Skill-driven extraction prompt\n\n"
        f"- skill_id: {skill_id}\n"
        f"- skill_version: {env_version}\n"
        f"- skill_repo_pin: {env_pin}\n\n"
    )

    return (
        skill_header
        + skill_body
        + "\n\n## Ontology anchors (re-injected)\n\n"
        + categories_block
        + "\n\n"
        + standard_names_block
        + "\n"
    )
