#!/usr/bin/env python3
"""Route-to-Service call matrix builder (NFM-3868, Pilot-B R2).

Read-only analysis: parses Python AST imports from all route modules
under ``nfm_db/api/`` and maps them to service modules under
``nfm_db/services/``.

Deliverables:
- Binary matrix (JSON + CSV)
- Clustered heatmap (PNG)
- Dead service candidates (0 route imports)
- Shared infra (imported by 10+ routes)

Usage:
    python scripts/route_service_matrix.py [--api-root <path>] [--output-dir <dir>]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.cluster import hierarchy
from scipy.spatial.distance import pdist


# ---------------------------------------------------------------------------
# AST import extraction
# ---------------------------------------------------------------------------


def extract_service_imports(
    filepath: Path,
    services_prefix: str = "nfm_db.services",
) -> set[str]:
    """Parse *filepath* and return service module names imported.

    Handles both ``from nfm_db.services.x import Y`` and
    ``import nfm_db.services.x``.

    Returns module paths relative to ``nfm_db.services`` (e.g.
    ``"material_service"``, ``"backup/retention"``).
    """
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return set()

    imported: set[str] = set()
    prefix_dot = services_prefix + "."

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith(prefix_dot):
                rel = node.module[len(prefix_dot):]
                imported.add(rel)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefix_dot):
                    rel = alias.name[len(prefix_dot):]
                    imported.add(rel)

    return imported


def collect_route_files(api_root: Path) -> list[Path]:
    """Return ``.py`` route files under ``api_root/api/`` (excl. ``__init__``)."""
    api_dir = api_root / "api"
    if not api_dir.is_dir():
        return []
    routes: list[Path] = []
    for py in sorted(api_dir.rglob("*.py")):
        if py.name == "__init__.py":
            continue
        routes.append(py)
    return routes


def collect_service_modules(services_root: Path) -> list[str]:
    """Return dotted-module paths relative to *services_root* (excl. ``__init__``)."""
    modules: list[str] = []
    for py in sorted(services_root.rglob("*.py")):
        if py.name == "__init__.py":
            continue
        rel = py.relative_to(services_root)
        modules.append(rel.with_suffix("").as_posix())
    return modules


# ---------------------------------------------------------------------------
# Matrix construction
# ---------------------------------------------------------------------------


def build_matrix(
    route_files: list[Path],
    service_modules: list[str],
    api_root: Path,
) -> tuple[pd.DataFrame, dict[str, set[str]]]:
    """Build binary DataFrame (routes × services) and raw import map."""
    route_imports: dict[str, set[str]] = {}
    for rf in route_files:
        short = rf.relative_to(api_root / "api").with_suffix("").as_posix()
        route_imports[short] = extract_service_imports(rf)

    route_names = sorted(route_imports.keys())
    svc_names = sorted(service_modules)

    matrix = np.zeros((len(route_names), len(svc_names)), dtype=np.int8)
    for i, rn in enumerate(route_names):
        for j, sn in enumerate(svc_names):
            if sn in route_imports[rn]:
                matrix[i, j] = 1

    df = pd.DataFrame(matrix, index=route_names, columns=svc_names)
    return df, route_imports


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def find_dead_services(df: pd.DataFrame) -> list[str]:
    """Return service modules with zero route imports."""
    return sorted(df.columns[df.sum(axis=0) == 0].tolist())


def find_shared_infra(
    df: pd.DataFrame, threshold: int = 10
) -> list[tuple[str, int]]:
    """Return (service, import_count) for services imported by >= *threshold* routes."""
    counts = df.sum(axis=0).sort_values(ascending=False)
    return [(svc, int(cnt)) for svc, cnt in counts.items() if cnt >= threshold]


def route_dependency_counts(df: pd.DataFrame) -> list[tuple[str, int]]:
    """Return (route, service_count) sorted descending."""
    counts = df.sum(axis=1).sort_values(ascending=False)
    return [(rt, int(cnt)) for rt, cnt in counts.items()]


# ---------------------------------------------------------------------------
# Heatmap with hierarchical clustering
# ---------------------------------------------------------------------------


def plot_clustered_heatmap(df: pd.DataFrame, output_path: Path) -> None:
    """Save a clustered heatmap PNG (active services only)."""
    active = df.loc[:, df.sum(axis=0) > 0]
    if active.empty:
        print("No active service imports to plot.", file=sys.stderr)
        return

    data = active.values.astype(float)

    if data.shape[0] > 2:
        row_linkage = hierarchy.linkage(pdist(data, metric="hamming"), method="average")
        row_order = hierarchy.leaves_list(row_linkage)
    else:
        row_order = list(range(data.shape[0]))

    if data.shape[1] > 2:
        col_linkage = hierarchy.linkage(pdist(data.T, metric="hamming"), method="average")
        col_order = hierarchy.leaves_list(col_linkage)
    else:
        col_order = list(range(data.shape[1]))

    clustered = data[np.ix_(row_order, col_order)]
    row_labels = [active.index[i] for i in row_order]
    col_labels = [active.columns[j] for j in col_order]

    fig_w = max(16, len(col_labels) * 0.22)
    fig_h = max(10, len(row_labels) * 0.28)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    cmap = matplotlib.colormaps["YlOrRd"]
    im = ax.imshow(
        clustered, aspect="auto", cmap=cmap, vmin=0, vmax=1, interpolation="nearest"
    )

    ax.set_xticks(range(len(col_labels)))
    ax.set_xticklabels(col_labels, rotation=90, fontsize=5, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=5)

    ax.set_title("Route → Service Call Matrix (clustered)", fontsize=12, pad=12)
    ax.set_xlabel("Service modules", fontsize=9)
    ax.set_ylabel("Route modules", fontsize=9)

    fig.colorbar(im, ax=ax, fraction=0.02, pad=0.04, label="Import (1=yes, 0=no)")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Heatmap saved: {output_path}")


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _infra_tiers(df: pd.DataFrame) -> dict[int, list[tuple[str, int]]]:
    """Return shared-infra grouped by import-count tiers."""
    counts = df.sum(axis=0).sort_values(ascending=False)
    tiers = {3: [], 5: [], 10: []}
    for svc, cnt in counts.items():
        c = int(cnt)
        if c >= 10:
            tiers[10].append((svc, c))
        elif c >= 5:
            tiers[5].append((svc, c))
        elif c >= 3:
            tiers[3].append((svc, c))
    return tiers


def generate_report(
    df: pd.DataFrame,
    dead: list[str],
    shared: list[tuple[str, int]],
    route_deps: list[tuple[str, int]],
    output_path: Path,
) -> None:
    """Write a markdown analysis report."""
    density = df.sum().sum() / (df.shape[0] * df.shape[1]) * 100
    active_svc = int((df.sum(axis=0) > 0).sum())
    svc_counts = df.sum(axis=0)
    single_use = int((svc_counts == 1).sum())
    tiers = _infra_tiers(df)

    lines: list[str] = [
        "# Route-to-Service Call Matrix Report (NFM-3868)",
        "",
        f"**Routes:** {df.shape[0]}  |  **Services:** {df.shape[1]}  |  "
        f"**Active (imported):** {active_svc}  |  "
        f"**Dead (0 imports):** {len(dead)}  |  "
        f"**Active edges:** {int(df.sum().sum())}  |  "
        f"**Matrix density:** {density:.1f}%",
        "",
        "### Import-Count Distribution",
        "",
        f"| Tier | Count |",
        f"|------|-------|",
        f"| 1 route (single-use) | {single_use} |",
        f"| 2–4 routes | {int(((svc_counts >= 2) & (svc_counts <= 4)).sum())} |",
        f"| 5–9 routes | {int(((svc_counts >= 5) & (svc_counts <= 9)).sum())} |",
        f"| 10+ routes | {int((svc_counts >= 10).sum())} |",
        "",
        "---",
        "",
        "## Dead Service Candidates (0 route imports)",
        "",
        f"**{len(dead)} services** are never directly imported by any route module.  ",
        "These may be: (a) internal pipeline/worker modules called via Celery tasks, ",
        "(b) sub-modules imported only by other service modules, or ",
        "(c) truly dead code.",
        "",
    ]
    if dead:
        for s in dead:
            lines.append(f"- `{s}`")
    else:
        lines.append("*(none)*")

    lines.extend(["", "---", "", "## Shared Infrastructure (tiered)", ""])

    for threshold, label in [(3, "≥3 route imports"), (5, "≥5 route imports"), (10, "≥10 route imports")]:
        items = tiers[threshold]
        lines.append(f"### {label} ({len(items)} services)")
        if items:
            for svc, cnt in items:
                bar = "█" * cnt
                lines.append(f"- `{svc}` — **{cnt}** routes  {bar}")
        else:
            lines.append("*(none at this tier)*")
        lines.append("")

    lines.extend(["---", "", "## Route Dependency Counts (descending)", ""])
    for rt, cnt in route_deps:
        lines.append(f"- `{rt}` — {cnt} services")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report saved: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent
        / "apps" / "api" / "src" / "nfm_db",
        help="Path to nfm_db package root",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "docs",
        help="Directory for output files",
    )
    args = parser.parse_args()

    api_root: Path = args.api_root
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    services_root = api_root / "services"

    # Collect
    route_files = collect_route_files(api_root)
    service_modules = collect_service_modules(services_root)
    print(f"Routes found: {len(route_files)}")
    print(f"Service modules found: {len(service_modules)}")

    # Build matrix
    df, _route_imports = build_matrix(route_files, service_modules, api_root)
    print(f"Matrix shape: {df.shape[0]}×{df.shape[1]}")
    print(f"Active edges: {int(df.sum().sum())}")

    # Analysis
    dead = find_dead_services(df)
    shared = find_shared_infra(df, threshold=10)
    route_deps = route_dependency_counts(df)

    print(f"\nDead service candidates ({len(dead)}):")
    for s in dead:
        print(f"  {s}")
    print(f"\nShared infrastructure (10+ imports, {len(shared)}):")
    for svc, cnt in shared:
        print(f"  {svc}: {cnt}")

    # Outputs
    json_path = output_dir / "route-service-matrix.json"
    matrix_data = {
        "routes": df.index.tolist(),
        "services": df.columns.tolist(),
        "matrix": df.values.tolist(),
        "dead_services": dead,
        "shared_infra": [[s, c] for s, c in shared],
        "route_dependency_counts": route_deps,
    }
    json_path.write_text(json.dumps(matrix_data, indent=2), encoding="utf-8")
    print(f"\nJSON matrix: {json_path}")

    csv_path = output_dir / "route-service-matrix.csv"
    df.to_csv(csv_path)
    print(f"CSV matrix: {csv_path}")

    heatmap_path = output_dir / "route-service-matrix-heatmap.png"
    plot_clustered_heatmap(df, heatmap_path)

    report_path = output_dir / "route-service-matrix.md"
    generate_report(df, dead, shared, route_deps, report_path)


if __name__ == "__main__":
    main()
