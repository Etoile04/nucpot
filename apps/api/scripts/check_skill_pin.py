#!/usr/bin/env python3
"""CI fail-closed guard for the skill repo pin (NFM-4547 / AC-8).

Runs in CI to verify ``EXTRACTION_SKILL_REPO_PIN`` + ``EXTRACTION_SKILL_VERSION``
match ``packages/skills-catalog/lock.yaml``. Exits non-zero on any drift so
the build fails before merging.

The script is intentionally dependency-light — it reads the YAML
directly so it can run in a bare CI image before the API deps install.
PyYAML is the only third-party import; everything else is stdlib.

Exit codes:
    0 — env vars absent (skill flag is OFF; no check needed)
    0 — env vars present and agree with lock file
    1 — env vars present but disagree with lock file (CI must fail)
    2 — lock file missing required keys (CI must fail)
    3 — lock file not found (CI must fail)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


_SHA1_HEX = re.compile(r"^[0-9a-f]{40}$")


def _read_yaml_minimal(path: Path) -> dict[str, object]:
    """Parse YAML without importing PyYAML.

    Supports the small subset this lock file uses: top-level mapping,
    nested mapping via indentation, ``key: value`` and ``key:`` lines.
    Falls back to PyYAML when available so behaviour matches the
    factory.
    """
    try:
        import yaml  # type: ignore[import-not-found]

        with path.open(encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
            return loaded if isinstance(loaded, dict) else {}
    except ImportError:
        pass

    out: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, out)]
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = value
    return out


def main() -> int:
    repo_root = Path(__file__).resolve()
    for _ in range(6):
        if (repo_root / "packages" / "skills-catalog" / "lock.yaml").is_file():
            break
        repo_root = repo_root.parent
    else:
        sys.stderr.write(
            "[check_skill_pin] lock file not found — expected packages/skills-catalog/lock.yaml\n"
        )
        return 3

    lock_path = repo_root / "packages" / "skills-catalog" / "lock.yaml"
    try:
        raw = _read_yaml_minimal(lock_path)
    except Exception as exc:
        sys.stderr.write(f"[check_skill_pin] failed to parse {lock_path}: {exc}\n")
        return 2

    catalog = raw.get("catalog") or {}
    upstream = (catalog or {}).get("upstream") or {}
    skills = raw.get("skills") or {}
    default_skill = catalog.get("default_skill") if isinstance(catalog, dict) else None
    default_meta = skills.get(default_skill, {}) if isinstance(skills, dict) else {}

    if not isinstance(upstream, dict) or not upstream.get("pin"):
        sys.stderr.write(
            f"[check_skill_pin] {lock_path} missing catalog.upstream.pin — fail closed\n"
        )
        return 2

    lock_pin = str(upstream["pin"])
    lock_version = str((default_meta or {}).get("version", "")) if isinstance(default_meta, dict) else ""

    env_pin = os.environ.get("EXTRACTION_SKILL_REPO_PIN", "").strip()
    env_version = os.environ.get("EXTRACTION_SKILL_VERSION", "").strip()

    # When the skill flag is off (the dark-launch default), no pin check
    # is required — exit 0 so CI does not gate the legacy path.
    flag = os.environ.get("EXTRACTION_SKILL_ENABLED", "").strip().lower()
    flag_on = flag in ("1", "true", "yes", "on")

    if not env_pin and not env_version and not flag_on:
        print("[check_skill_pin] skill flag off; pin check skipped")
        return 0

    errors: list[str] = []

    if env_pin and not _SHA1_HEX.match(env_pin):
        errors.append(
            f"EXTRACTION_SKILL_REPO_PIN={env_pin!r} is not a 40-char hex SHA"
        )

    if env_pin and env_pin != lock_pin:
        errors.append(
            f"EXTRACTION_SKILL_REPO_PIN drift: env={env_pin!r} vs lock={lock_pin!r}"
        )

    if env_version and lock_version and env_version != lock_version:
        errors.append(
            f"EXTRACTION_SKILL_VERSION drift: env={env_version!r} vs lock={lock_version!r}"
        )

    if env_pin and lock_pin.startswith("0" * 40):
        errors.append(
            "lock file upstream.pin is the zero placeholder; "
            "set a real SHA before flipping EXTRACTION_SKILL_ENABLED=true"
        )

    if errors:
        sys.stderr.write("[check_skill_pin] CI FAIL-CLOSED:\n")
        for err in errors:
            sys.stderr.write(f"  - {err}\n")
        sys.stderr.write(
            f"  lock file: {lock_path}\n"
            f"  env: PIN={env_pin!r} VERSION={env_version!r} ENABLED={flag!r}\n"
        )
        return 1

    print(
        "[check_skill_pin] OK — env matches lock file "
        f"(pin={env_pin or lock_pin}, version={env_version or lock_version})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())