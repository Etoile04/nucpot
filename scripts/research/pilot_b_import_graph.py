#!/usr/bin/env python3
"""Static import-graph analysis for apps/api/src/nfm_db/services/.

Reads the AST of every ``*.py`` file under the services package, builds a
directed graph where nodes are intra-package modules and edges are static
``import`` / ``from-import`` references, and emits four markdown tables:

1. Fan-in top-20 — most-imported services.
2. Fan-out top-20 — biggest consumers (files that import the most siblings).
3. Circular dependency chains — Tarjan strongly connected components of size
   ``>= 2`` plus self-loops.
4. Cross ``providers/`` import edges — edges where either endpoint lives in
   the ``providers/`` subpackage and the other does not, plus edges that
   bridge two distinct ``providers/`` modules.

This script is read-only — it never modifies source files. It is the
deliverable for NFM-3867 (PILOT-B B-R1) and feeds the B-D1 cut-axis decision
recorded on parent NFM-3865.

Usage::

    python3 scripts/research/pilot_b_import_graph.py [--repo PATH] [--out PATH]

Defaults: ``--repo .`` (current working directory must be the repo root) and
``--out docs/research/pilot-b-import-graph.md``.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

PACKAGE_ROOT = "nfm_db.services"
SERVICES_REL = Path("apps/api/src/nfm_db/services")
PROVIDERS_DIR_NAME = "providers"
TOP_N = 20


@dataclass(frozen=True)
class Edge:
    consumer: str
    producer: str

    def is_cross_providers(self) -> bool:
        """True when this edge crosses the providers/ boundary in either direction.

        Two flavors:
          a) one side in providers/, the other outside providers/.
          b) both sides in providers/ but in distinct provider modules.
        Self-edges inside providers/ are excluded — those are intra-module
        noise (a file importing itself is impossible, so this is defensive).
        """
        c_in = self.consumer.startswith(f"{PACKAGE_ROOT}.{PROVIDERS_DIR_NAME}.")
        p_in = self.producer.startswith(f"{PACKAGE_ROOT}.{PROVIDERS_DIR_NAME}.")
        if c_in != p_in:
            return True
        if c_in and p_in and self.consumer != self.producer:
            return True
        return False


@dataclass
class Graph:
    """Directed import graph for the services package.

    Nodes are dotted module names like ``nfm_db.services.auth_service`` or
    ``nfm_db.services.providers.openkim``. Self-edges and intra-statement
    duplicates are dropped — each consumer-producer pair contributes at most
    one edge regardless of how many names were imported from the producer.
    """

    nodes: set[str] = field(default_factory=set)
    edges: set[Edge] = field(default_factory=set)
    _out: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _in: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_node(self, module: str) -> None:
        self.nodes.add(module)

    def add_edge(self, consumer: str, producer: str) -> None:
        if consumer == producer:
            return
        edge = Edge(consumer, producer)
        if edge in self.edges:
            return
        self.edges.add(edge)
        self._out[consumer].add(producer)
        self._in[producer].add(consumer)

    def fan_in(self, node: str) -> int:
        return len(self._in.get(node, ()))

    def fan_out(self, node: str) -> int:
        return len(self._out.get(node, ()))

    def in_neighbors(self, node: str) -> set[str]:
        return set(self._in.get(node, ()))

    def out_neighbors(self, node: str) -> set[str]:
        return set(self._out.get(node, ()))


def discover_modules(services_dir: Path) -> dict[Path, str]:
    """Map every ``*.py`` file under ``services_dir`` to its dotted module name.

    Returns ``{absolute_path: dotted_module}``. ``__init__.py`` files are
    mapped to the package itself (e.g. ``nfm_db.services``,
    ``nfm_db.services.providers``) so that ``from nfm_db.services import x``
    and ``from . import x`` both resolve cleanly.
    """
    # Anchor the dotted path at the ``src`` directory so module names include
    # the full ``nfm_db.services.*`` prefix expected by import statements.
    package_anchor = services_dir.parent.parent  # …/src
    mapping: dict[Path, str] = {}
    for py in sorted(services_dir.rglob("*.py")):
        rel = py.relative_to(package_anchor)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        dotted = ".".join(parts)
        if not dotted.startswith(PACKAGE_ROOT):
            # Should be impossible given SERVICES_REL, but guard anyway.
            continue
        mapping[py] = dotted
    return mapping


def resolve_producer(imported: str, consumer_module: str, modules: set[str]) -> str | None:
    """Map an import target to a module name inside ``modules``.

    Walks progressively shorter prefixes of the imported dotted path until it
    finds a known module. This handles both ``import nfm_db.services.foo``
    and ``from nfm_db.services.foo import Bar`` with the same logic.

    Returns ``None`` for imports that point outside the services package or
    to modules we cannot resolve.
    """
    if not imported:
        return None
    parts = imported.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in modules:
            return candidate
    # Relative import: ``from .x import y`` or ``from .. import z``.
    if imported.startswith("."):
        consumer_parts = consumer_module.split(".")
        # Drop the file's own segment; consumer is a module, not a file path.
        base_parts = consumer_parts[:-1]
        depth = 0
        for ch in imported:
            if ch == ".":
                depth += 1
            else:
                break
        # ``from . import x`` → depth 1, target list starts after the dots.
        remainder = imported[depth:]
        up = base_parts[: max(0, len(base_parts) - (depth - 1))]
        if remainder:
            tail_parts = remainder.split(".")
            full = up + tail_parts
        else:
            full = up
        for end2 in range(len(full), 0, -1):
            cand = ".".join(full[:end2])
            if cand in modules:
                return cand
    return None


def collect_imports(path: Path, consumer: str, modules: set[str]) -> Iterable[Edge]:
    """Yield ``Edge`` objects for every intra-package import in ``path``."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # Tolerate broken files — we are a read-only analyzer.
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                producer = resolve_producer(alias.name, consumer, modules)
                if producer is not None:
                    yield Edge(consumer, producer)
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            # Resolve the *module* portion first; each imported name contributes
            # an edge to the resolved module, not to individual submodules.
            producer = resolve_producer(module_name, consumer, modules)
            if producer is not None:
                yield Edge(consumer, producer)
            elif node.names:
                # ``from pkg import a, b`` where ``pkg`` itself isn't in our
                # graph but ``pkg.a`` might be (rare but possible). Skip —
                # the deliverable counts only direct producer relationships.
                pass


def build_graph(repo_root: Path) -> Graph:
    services_dir = (repo_root / SERVICES_REL).resolve()
    if not services_dir.is_dir():
        raise SystemExit(f"services directory missing: {services_dir}")
    modules_map = discover_modules(services_dir)
    modules_set = set(modules_map.values())
    graph = Graph()
    for module in modules_set:
        graph.add_node(module)
    for path, consumer in modules_map.items():
        for edge in collect_imports(path, consumer, modules_set):
            graph.add_edge(edge.consumer, edge.producer)
    return graph


# ---------------------------------------------------------------------------
# Tarjan SCC
# ---------------------------------------------------------------------------


def tarjan_scc(nodes: set[str], succ: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan strongly connected components.

    Returns a list of SCCs, each a list of node names. Order within an SCC
    follows discovery; SCCs themselves are returned in reverse-finish order
    (matching the recursive algorithm).
    """
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    sccs: list[list[str]] = []

    for root in nodes:
        if root in indices:
            continue
        # Iterative DFS frame: (node, iterator over successors).
        work: list[tuple[str, list[str]]] = [(root, sorted(succ.get(root, ())))]
        indices[root] = index_counter[0]
        lowlinks[root] = index_counter[0]
        index_counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, it = work[-1]
            if it:
                w = it.pop()
                if w not in indices:
                    indices[w] = index_counter[0]
                    lowlinks[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, sorted(succ.get(w, ()))))
                elif w in on_stack:
                    lowlinks[v] = min(lowlinks[v], indices[w])
            else:
                # Finished v — pop and propagate lowlink.
                if lowlinks[v] == indices[v]:
                    component: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == v:
                            break
                    sccs.append(component)
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[v])
    return sccs


def cycle_sccs(graph: Graph) -> list[list[str]]:
    """Return SCCs of size ``>= 2`` and singleton self-loops as cycles."""
    succ = {node: graph.out_neighbors(node) for node in graph.nodes}
    raw = tarjan_scc(graph.nodes, succ)
    cycles: list[list[str]] = []
    for component in raw:
        if len(component) >= 2:
            cycles.append(sorted(component))
            continue
        # Singleton SCC — check for a self-loop (defensive; we already drop them).
        only = component[0]
        if only in graph.out_neighbors(only):
            cycles.append([only])
    return sorted(cycles, key=lambda c: (-len(c), c))


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Render a GitHub-flavored markdown table."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def short(name: str) -> str:
    """Trim the common prefix ``nfm_db.services.`` for display."""
    prefix = f"{PACKAGE_ROOT}."
    return name[len(prefix):] if name.startswith(prefix) else name


def render(graph: Graph) -> str:
    lines: list[str] = []
    lines.append("# NFM-3867 — services import graph")
    lines.append("")
    lines.append(
        "Static AST-based analysis of the import relationships inside "
        "`apps/api/src/nfm_db/services/`. Generated by "
        "`scripts/research/pilot_b_import_graph.py`. Feeds the **B-D1** "
        "cut-axis decision on parent **NFM-3865**."
    )
    lines.append("")

    # Summary block.
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Modules analyzed: **{len(graph.nodes)}**")
    lines.append(f"- Distinct import edges: **{len(graph.edges)}**")
    cycle_count = sum(1 for c in cycle_sccs(graph))
    lines.append(f"- Strongly connected components of size ≥ 2 (cycles): **{cycle_count}**")
    cross = sorted(
        (e for e in graph.edges if e.is_cross_providers()),
        key=lambda e: (e.consumer, e.producer),
    )
    lines.append(f"- Cross-`providers/` edges: **{len(cross)}**")
    lines.append("")

    # Fan-in (top 20).
    fan_in_sorted = sorted(graph.nodes, key=lambda n: (-graph.fan_in(n), n))
    fan_in_rows: list[list[str]] = []
    for rank, node in enumerate(fan_in_sorted[:TOP_N], start=1):
        in_count = graph.fan_in(node)
        # Top 5 inbound neighbors (sorted by their own fan-in, then name) so
        # the table tells you *who* depends on the heavy hitter, not just the count.
        top_consumers = sorted(
            graph.in_neighbors(node), key=lambda n: (-graph.fan_in(n), n)
        )[:5]
        fan_in_rows.append([
            str(rank),
            f"`{short(node)}`",
            str(in_count),
            ", ".join(f"`{short(c)}`" for c in top_consumers) or "—",
        ])
    lines.append(f"## Fan-in — top {TOP_N} most-imported services")
    lines.append("")
    lines.append(
        "In-degree of the directed graph: how many *distinct* other service "
        "files statically import each module. The rightmost column lists the "
        "top 5 inbound consumers for context (sorted by their own fan-in)."
    )
    lines.append("")
    lines.extend(
        render_table(
            ["#", "module", "fan-in", "top consumers (in-neighbors)"],
            fan_in_rows,
        )
    )
    lines.append("")

    # Fan-out (top 20).
    fan_out_sorted = sorted(graph.nodes, key=lambda n: (-graph.fan_out(n), n))
    fan_out_rows: list[list[str]] = []
    for rank, node in enumerate(fan_out_sorted[:TOP_N], start=1):
        out_count = graph.fan_out(node)
        top_producers = sorted(
            graph.out_neighbors(node), key=lambda n: (-graph.fan_in(n), n)
        )[:5]
        fan_out_rows.append([
            str(rank),
            f"`{short(node)}`",
            str(out_count),
            ", ".join(f"`{short(p)}`" for p in top_producers) or "—",
        ])
    lines.append(f"## Fan-out — top {TOP_N} biggest consumers")
    lines.append("")
    lines.append(
        "Out-degree: how many *distinct* sibling service modules each file "
        "statically imports. High fan-out modules are the natural candidates "
        "for **facade / re-export** refactors if B-D1 chooses a vertical cut."
    )
    lines.append("")
    lines.extend(
        render_table(
            ["#", "module", "fan-out", "top producers (out-neighbors)"],
            fan_out_rows,
        )
    )
    lines.append("")

    # Cycles (Tarjan SCC).
    cycles = cycle_sccs(graph)
    lines.append("## Circular dependency chains (Tarjan SCC)")
    lines.append("")
    if not cycles:
        lines.append("_No strongly connected components of size ≥ 2 detected._")
    else:
        lines.append(
            "Each row is one SCC of size ≥ 2 — every module in the set can "
            "reach every other via static imports. Self-loops would also "
            "appear here but were dropped during edge insertion. "
            "Cycle nodes are listed alphabetically; sort by size to find the "
            "largest tangles first."
        )
        lines.append("")
        cycle_rows: list[list[str]] = []
        for idx, component in enumerate(sorted(cycles, key=lambda c: (-len(c), c)), start=1):
            cycle_rows.append([
                str(idx),
                str(len(component)),
                ", ".join(f"`{short(n)}`" for n in sorted(component)),
            ])
        lines.extend(
            render_table(["#", "size", "modules"], cycle_rows)
        )
    lines.append("")

    # Cross providers edges.
    cross_edges = sorted(
        (e for e in graph.edges if e.is_cross_providers()),
        key=lambda e: (short(e.consumer), short(e.producer)),
    )
    lines.append("## Cross-`providers/` import edges")
    lines.append("")
    lines.append(
        "Edges that cross the `providers/` boundary — either (a) one endpoint "
        "lives in `providers/` and the other does not, or (b) both endpoints "
        "live in `providers/` but in different provider modules. Useful for "
        "spotting consumers that hard-code a specific provider rather than "
        "going through the abstract base."
    )
    lines.append("")
    if not cross_edges:
        lines.append("_No cross-`providers/` edges detected._")
    else:
        cross_rows: list[list[str]] = []
        for idx, edge in enumerate(cross_edges, start=1):
            cross_rows.append([
                str(idx),
                f"`{short(edge.consumer)}`",
                "→",
                f"`{short(edge.producer)}`",
            ])
        lines.extend(
            render_table(["#", "consumer", "", "producer"], cross_rows)
        )
    lines.append("")

    # Methodology footer.
    lines.append("## Methodology")
    lines.append("")
    lines.append(
        "1. Walk every `*.py` file under `apps/api/src/nfm_db/services/`, "
        "excluding `__pycache__`.\n"
        "2. Map each file to its dotted module name relative to "
        "`apps/api/src/`. `__init__.py` files map to the package itself.\n"
        "3. Parse each file with `ast.parse`; collect every `ast.Import` and "
        "`ast.ImportFrom`. Resolve each target to a module inside the "
        "services package by walking progressively shorter dotted prefixes "
        "until a known module is found. Both absolute imports "
        "(`from nfm_db.services.foo import Bar`) and relative imports "
        "(`from .foo import Bar`) are handled.\n"
        "4. Build a directed graph where each `(consumer, producer)` pair "
        "contributes at most one edge, even if multiple names are imported "
        "from the same producer. Self-edges are dropped.\n"
        "5. Fan-in = in-degree, fan-out = out-degree. Top-N tables list "
        "the top 5 neighbors by their own in-degree so the reader sees who "
        "the heavy hitters are coupled to.\n"
        "6. Tarjan SCC identifies circular dependency chains; SCCs of "
        "size ≥ 2 are reported as cycles.\n"
        "7. Cross-`providers/` edges are the subset where at least one "
        "endpoint is under `nfm_db.services.providers.` and the other is "
        "not, or both are in `providers/` but in different modules."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--repo",
        default=".",
        help="Path to the nucpot repo root (default: current directory).",
    )
    parser.add_argument(
        "--out",
        default="docs/research/pilot-b-import-graph.md",
        help="Markdown output path, relative to --repo (default: docs/research/pilot-b-import-graph.md).",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    graph = build_graph(repo_root)
    output = repo_root / args.out
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(graph), encoding="utf-8")
    print(
        f"wrote {output.relative_to(repo_root)}: "
        f"{len(graph.nodes)} modules, {len(graph.edges)} edges, "
        f"{sum(1 for _ in cycle_sccs(graph))} cycles, "
        f"{sum(1 for e in graph.edges if e.is_cross_providers())} cross-providers edges",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
