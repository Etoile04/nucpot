#!/usr/bin/env python3
"""Schema compatibility check for NFMD Batch 1 CI (B1.7 / ADR-NFM-817-5).

Compares the current pydantic extraction schemas against a frozen baseline
snapshot and reports any breaking changes.

Breaking changes detected:
  * A required field is removed or becomes optional
  * A field's type changes
  * A required field is added (callers won't supply it)
  * An enum / literal value is removed from a constrained field

Non-breaking additions (optional fields, new union members) are reported
as info but do not fail the check.

Usage:
    python scripts/check_schema_compat.py \\
        --baseline schemas/.compat-baseline/ \\
        --source apps/api/src/nfm_db/schemas/extraction.py \\
                 apps/api/src/nfm_db/schemas/vision_extraction.py \\
        [--fail-on-warn]

Exit code: 0 if no breaking changes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _configure_sys_path(extra_paths: Iterable[Path]) -> None:
    """Prepend extra paths to sys.path so relative imports resolve."""
    for p in extra_paths:
        s = str(p.resolve())
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module_from_path(path: Path):
    """Load a python source file as a module.

    For files organized inside a package (e.g. ``nfm_db/schemas/extraction.py``),
    use the dotted module path so relative imports like ``from .vision_extraction
    import TableData`` resolve against the package that owns them.
    """
    path = path.resolve()
    package_root = _find_package_root(path)
    if package_root is not None:
        # Insert the directory *above* the package so 'pkg.mod' imports work.
        parent = package_root.parent
        parent_str = str(parent)
        if parent_str not in sys.path:
            sys.path.insert(0, parent_str)
        dotted = _dotted_for(path, package_root)
        try:
            return importlib.import_module(dotted)
        except ImportError:
            pass  # Fall back to direct loading below.

    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_package_root(path: Path) -> Path | None:
    """Walk up from ``path`` and return the first dir containing __init__.py."""
    current = path.parent
    while current != current.parent:
        if (current / "__init__.py").exists():
            return current
        current = current.parent
    return None


def _dotted_for(path: Path, package_root: Path) -> str:
    """Build the dotted module name for ``path`` relative to ``package_root``."""
    rel = path.relative_to(package_root.parent)
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(p for p in parts if p != "__init__")


def _collect_pydantic_models(module) -> dict[str, type]:
    """Return {model_name: model_class} for every BaseModel subclass."""
    from pydantic import BaseModel

    models: dict[str, type] = {}
    for name, obj in vars(module).items():
        if name.startswith("_"):
            continue
        if not isinstance(obj, type):
            continue
        if obj is BaseModel:
            continue
        if issubclass(obj, BaseModel):
            models[name] = obj
    return models


def _normalize_type(tp: Any) -> str:
    """Return a canonical, comparable string for an annotation."""
    return repr(tp).replace(" ", "")


def model_signature(model: type) -> dict[str, Any]:
    """Build a JSON-serializable snapshot of a model.

    Only what we need for compatibility checks: field names, types,
    required/optional, and any Literal value lists we can find.
    """
    out: dict[str, Any] = {"fields": {}, "literals": {}}
    for fname, finfo in model.model_fields.items():
        out["fields"][fname] = {
            "required": finfo.is_required(),
            "type": _normalize_type(finfo.annotation),
            "default": (
                None
                if finfo.default is None or finfo.default is ...
                else repr(finfo.default)
            ),
        }
        annotation = finfo.annotation
        annotation_repr = repr(annotation)
        if "Literal" in annotation_repr or "Enum" in annotation_repr:
            out["literals"][fname] = annotation_repr
    return out


@dataclass
class Diff:
    breaking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.breaking


def _compare_field(
    name: str,
    base: dict[str, Any],
    current: dict[str, Any],
) -> tuple[list[str], list[str]]:
    breaking: list[str] = []
    warnings: list[str] = []
    if "required" not in current:
        breaking.append(
            f"[{name}] field removed (was present in baseline)"
        )
        return breaking, warnings
    if base["required"] and not current["required"]:
        breaking.append(
            f"[{name}] was required, now optional (breaking for callers)"
        )
    elif not base["required"] and current["required"]:
        breaking.append(
            f"[{name}] was optional, now required (breaking for callers)"
        )
    if base["type"] != current["type"]:
        breaking.append(
            f"[{name}] type changed: {base['type']} -> {current['type']}"
        )
    return breaking, warnings


def _extract_literal_values(literal_repr: str) -> set[str]:
    """Extract the set of literal values from a `Literal[...]` repr."""
    if "Literal[" not in literal_repr:
        return set()
    inside = literal_repr.split("Literal[", 1)[1].rsplit("]", 1)[0]
    return {v.strip().strip("'\"") for v in inside.split(",") if v.strip()}


def _compare_literal(
    name: str,
    base_lit: str,
    current_lit: str,
) -> tuple[list[str], list[str]]:
    breaking: list[str] = []
    warnings: list[str] = []
    base_values = _extract_literal_values(base_lit)
    current_values = _extract_literal_values(current_lit)
    removed = base_values - current_values
    if removed:
        breaking.append(
            f"[{name}] removed Literal values: {sorted(removed)}"
        )
    added = current_values - base_values
    if added:
        warnings.append(
            f"[{name}] added Literal values: {sorted(added)}"
        )
    return breaking, warnings


def diff_model(
    model_name: str,
    base: dict[str, Any],
    current: dict[str, Any],
) -> Diff:
    breaking: list[str] = []
    warnings: list[str] = []
    base_fields = base["fields"]
    curr_fields = current["fields"]

    for fname, base_f in base_fields.items():
        curr_f = curr_fields.get(fname)
        if curr_f is None:
            breaking.append(f"[{model_name}.{fname}] removed")
            continue
        sub_b, sub_w = _compare_field(
            f"{model_name}.{fname}", base_f, curr_f
        )
        breaking.extend(sub_b)
        warnings.extend(sub_w)

    for fname, curr_f in curr_fields.items():
        if fname in base_fields:
            continue
        path = f"{model_name}.{fname}"
        if curr_f.get("required"):
            breaking.append(
                f"[{path}] added as required (callers cannot supply)"
            )
        else:
            warnings.append(f"[{path}] added (non-breaking)")

    for fname, base_lit in base.get("literals", {}).items():
        curr_lit = current.get("literals", {}).get(fname)
        if curr_lit is None:
            breaking.append(
                f"[{model_name}.{fname}] lost Literal constraint"
            )
            continue
        sub_b, sub_w = _compare_literal(
            f"{model_name}.{fname}", base_lit, curr_lit
        )
        breaking.extend(sub_b)
        warnings.extend(sub_w)

    return Diff(breaking=breaking, warnings=warnings)


def load_baseline(baseline_dir: Path) -> dict[str, dict[str, Any]]:
    """Load baseline snapshots under <baseline_dir>/<model>.json."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(baseline_dir.glob("*.json")):
        out[path.stem] = json.loads(path.read_text())
    return out


def snapshot_current(sources: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Build a snapshot dict {model_name: signature} across all sources."""
    out: dict[str, dict[str, Any]] = {}
    for src in sources:
        try:
            module = _load_module_from_path(src)
        except Exception as exc:
            raise SystemExit(f"Failed to load {src}: {exc}") from exc
        for name, model in _collect_pydantic_models(module).items():
            out[name] = model_signature(model)
    return out


def write_baseline(
    snapshot: dict[str, dict[str, Any]],
    baseline_dir: Path,
) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    for name, sig in snapshot.items():
        (baseline_dir / f"{name}.json").write_text(
            json.dumps(sig, indent=2, sort_keys=True) + "\n"
        )


def _format(model: str, diff: Diff) -> str:
    lines = [f"== {model} =="]
    if diff.breaking:
        lines.append("  breaking:")
        for m in diff.breaking:
            lines.append(f"    - {m}")
    if diff.warnings:
        lines.append("  info:")
        for m in diff.warnings:
            lines.append(f"    - {m}")
    if not diff.breaking and not diff.warnings:
        lines.append("  (no changes)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Path to baseline snapshot dir (contains <Model>.json per model).",
    )
    parser.add_argument(
        "--source",
        type=Path,
        action="append",
        required=True,
        help="Python source file(s) defining the current pydantic models.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite baseline snapshot with current signature and exit.",
    )
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Treat info-level (non-breaking) additions as failures too.",
    )
    parser.add_argument(
        "--sys-path",
        action="append",
        type=Path,
        default=[],
        help="Extra directories to add to sys.path before importing sources "
             "(repeatable; helps when modules live under a real package).",
    )
    args = parser.parse_args(argv)

    if args.sys_path:
        _configure_sys_path(args.sys_path)

    if args.update_baseline:
        snapshot = snapshot_current(args.source)
        write_baseline(snapshot, args.baseline)
        print(
            f"Wrote {len(snapshot)} baselines to {args.baseline}",
            file=sys.stderr,
        )
        return 0

    baseline = load_baseline(args.baseline)
    current = snapshot_current(args.source)

    ok = True
    models = sorted(set(baseline) | set(current))
    for model in models:
        base_sig = baseline.get(model)
        curr_sig = current.get(model)
        if base_sig is None:
            print(f"== {model} == (new)")
            ok = False
            continue
        if curr_sig is None:
            print(f"BREAKING: model {model} removed from current sources")
            ok = False
            continue
        d = diff_model(model, base_sig, curr_sig)
        if d.breaking:
            ok = False
        if args.fail_on_warn and d.warnings:
            ok = False
        print(_format(model, d))

    if ok:
        print("\nOK: no breaking changes", file=sys.stderr)
        return 0
    print("\nFAIL: breaking changes detected", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
