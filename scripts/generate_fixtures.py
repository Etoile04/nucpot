#!/usr/bin/env python3
"""Generate the Batch 1 golden fixture corpus (B1.6 / ADR-NFM-817-5).

Produces 50 nuclear-materials paper fixtures under
    apps/api/tests/fixtures/extraction/<figure_type>/<paper_id>/
with a deterministic seed so every regeneration is byte-identical.

Each fixture contains:
    - image.png           (synthetic placeholder page image)
    - ground_truth.json   (bounding box, plot/table content, confidence)

Coverage targets (from the spec):
    * 20 plots
    * 15 tables
    * 10 microstructure
    * 5 diagrams

Usage:
    python scripts/generate_fixtures.py \\
        --output apps/api/tests/fixtures/extraction \\
        [--seed 42] \\
        [--counts plots=20 tables=15 microstructure=10 diagrams=5]
"""
from __future__ import annotations

import argparse
import json
import random
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

FIGURE_TYPES = ("plot", "table", "microstructure", "diagram")
DEFAULT_COUNTS = {"plot": 20, "table": 15, "microstructure": 10, "diagram": 5}
IMAGE_WIDTH = 640
IMAGE_HEIGHT = 480


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    length = struct.pack(">I", len(payload))
    crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
    return length + tag + payload + struct.pack(">I", crc)


def _synthetic_png(width: int, height: int, seed: int, fig_type: str) -> bytes:
    """Make a tiny deterministic PNG without external deps.

    Distinct figure types get distinct palettes so fixture reviews can
    eyeball that the corpus is shaped correctly. Content is otherwise
    synthetic — these are placeholders that prove the pipeline.
    """
    rng = random.Random(f"{fig_type}-{seed}")
    palette = {
        "plot":            [(245, 245, 245), (35, 70, 130),  (220, 60, 60),  (60, 130, 60)],
        "table":           [(250, 250, 250), (220, 220, 220), (60, 60, 60),   (130, 130, 130)],
        "microstructure":  [(240, 240, 240), (90, 90, 90),    (50, 50, 50),   (160, 160, 160)],
        "diagram":         [(250, 250, 250), (200, 140, 50),  (80, 80, 80),   (60, 90, 130)],
    }[fig_type]
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            band = (x + int(seed % 11) + int(y * 0.05)) % len(palette)
            base = palette[band]
            jitter = rng.randint(-15, 15)
            r = max(0, min(255, base[0] + jitter))
            g = max(0, min(255, base[1] + jitter))
            b = max(0, min(255, base[2] + jitter))
            raw.extend((r, g, b))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return (
        sig
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


NUCLEAR_SYSTEMS = (
    "UO2", "MOX", "Zr-4", "Inconel-718", "HT9", "F82H", "SiC-SiC",
    "BaFe12O19", "WC-Co", "BeO", "Li2O-Al2O3-SiO2",
)


@dataclass
class Fixture:
    paper_id: str
    figure_type: str
    fixture_dir: Path
    ground_truth: dict


def _axis(label: str, unit: str, count: int, scale: str, lo: float, hi: float, rng: random.Random):
    values = [
        round(lo + (hi - lo) * i / max(count - 1, 1), 4)
        for i in range(count)
    ]
    return {
        "label": label,
        "unit": unit,
        "values": values,
        "scale": scale,
    }


def _series(name: str, count: int, lo: float, hi: float, rng: random.Random):
    values = [round(rng.uniform(lo, hi), 4) for _ in range(count)]
    return {
        "name": name,
        "values": values,
        "color": rng.choice(("red", "blue", "green", "black")),
        "marker_style": rng.choice(("circle", "square", "triangle")),
    }


def _bbox(rng: random.Random) -> dict:
    x = round(rng.uniform(40, 120), 1)
    y = round(rng.uniform(40, 80), 1)
    w = round(rng.uniform(420, 540), 1)
    h = round(rng.uniform(280, 380), 1)
    return {"x": x, "y": y, "width": w, "height": h}


def _build_plot(paper_id: str, idx: int, rng: random.Random) -> dict:
    count = rng.choice([8, 10, 12])
    x_lo, x_hi = rng.uniform(0, 500), rng.uniform(800, 1500)
    y_lo, y_hi = rng.uniform(0, 100), rng.uniform(500, 1500)
    n_series = rng.randint(1, 3)
    return {
        "figure_type": "plot",
        "title": f"Figure {idx + 1}: {rng.choice(NUCLEAR_SYSTEMS)} response vs temperature",
        "bounding_box": _bbox(rng),
        "plot_data": {
            "title": f"{rng.choice(NUCLEAR_SYSTEMS)} tensile response",
            "plot_type": rng.choice(("line", "scatter", "bar")),
            "x_axis": _axis("Temperature", "K", count, "linear", x_lo, x_hi, rng),
            "y_axis": _axis("Stress", "MPa", count, "linear", y_lo, y_hi, rng),
            "series": [
                _series(f"sample {i}", count, y_lo * 0.8, y_hi * 1.2, rng)
                for i in range(n_series)
            ],
            "legend_entries": [f"sample {i}" for i in range(n_series)],
            "annotations": [],
            "confidence": round(rng.uniform(0.78, 0.97), 3),
        },
        "source_image_path": f"apps/api/tests/fixtures/extraction/plot/{paper_id}/image.png",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "extraction_time_ms": round(rng.uniform(120, 420), 1),
        "fallback_used": False,
        "paper_id": paper_id,
    }


def _build_table(paper_id: str, idx: int, rng: random.Random) -> dict:
    n_cols = rng.choice([3, 4, 5])
    n_rows = rng.randint(6, 12)
    headers = ["temperature_K", "stress_MPa", "strain_pct", "youngs_modulus_GPa", "density_gcc"][:n_cols]
    rows = []
    for r in range(n_rows):
        temp = round(rng.uniform(300, 1200), 1)
        rows.append([
            {"value": str(temp), "row_span": 1, "col_span": 1, "is_header": False, "confidence": 1.0},
            {"value": str(round(rng.uniform(50, 800), 2)), "row_span": 1, "col_span": 1, "is_header": False, "confidence": 1.0},
            {"value": str(round(rng.uniform(0.1, 25.0), 2)), "row_span": 1, "col_span": 1, "is_header": False, "confidence": 1.0},
            {"value": str(round(rng.uniform(50, 250), 1)), "row_span": 1, "col_span": 1, "is_header": False, "confidence": 1.0},
            {"value": str(round(rng.uniform(1.0, 13.5), 3)), "row_span": 1, "col_span": 1, "is_header": False, "confidence": 1.0},
        ][:n_cols])
    return {
        "figure_type": "table",
        "title": f"Table {idx + 1}: {rng.choice(NUCLEAR_SYSTEMS)} mechanical properties",
        "bounding_box": _bbox(rng),
        "table_data": {
            "title": f"{rng.choice(NUCLEAR_SYSTEMS)} mechanical properties",
            "headers": {
                "columns": headers,
                "sub_headers": None,
            },
            "rows": rows,
            "num_columns": n_cols,
            "num_rows": n_rows,
            "has_merged_cells": False,
            "notes": [],
            "confidence": round(rng.uniform(0.85, 0.98), 3),
        },
        "source_image_path": f"apps/api/tests/fixtures/extraction/table/{paper_id}/image.png",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "extraction_time_ms": round(rng.uniform(180, 520), 1),
        "fallback_used": False,
        "paper_id": paper_id,
    }


def _build_micro(paper_id: str, idx: int, rng: random.Random) -> dict:
    count = rng.randint(8, 14)
    return {
        "figure_type": "microstructure",
        "title": f"Micrograph {idx + 1}: {rng.choice(NUCLEAR_SYSTEMS)} grain distribution",
        "bounding_box": _bbox(rng),
        "plot_data": {
            "title": "grain size distribution",
            "plot_type": "bar",
            "x_axis": _axis("Grain size", "um", count, "linear", 0.1, 25.0, rng),
            "y_axis": _axis("Frequency", "%", count, "linear", 0, 30, rng),
            "series": [_series("frequency", count, 0, 30, rng)],
            "legend_entries": ["frequency"],
            "annotations": [],
            "confidence": round(rng.uniform(0.72, 0.93), 3),
        },
        "source_image_path": f"apps/api/tests/fixtures/extraction/microstructure/{paper_id}/image.png",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "extraction_time_ms": round(rng.uniform(120, 380), 1),
        "fallback_used": False,
        "paper_id": paper_id,
    }


def _build_diagram(paper_id: str, idx: int, rng: random.Random) -> dict:
    count = rng.randint(5, 9)
    n_series = rng.randint(2, 4)
    return {
        "figure_type": "diagram",
        "title": f"Diagram {idx + 1}: {rng.choice(NUCLEAR_SYSTEMS)} process flow",
        "bounding_box": _bbox(rng),
        "plot_data": {
            "title": "process flow diagram",
            "plot_type": "line",
            "x_axis": _axis("Step", "", count, "linear", 1, count, rng),
            "y_axis": _axis("Temperature", "K", count, "linear", 300, 1500, rng),
            "series": [
                _series(f"path {i}", count, 300, 1500, rng)
                for i in range(n_series)
            ],
            "legend_entries": [f"path {i}" for i in range(n_series)],
            "annotations": [
                "inlet",
                "outlet",
            ][: rng.randint(0, 2)],
            "confidence": round(rng.uniform(0.7, 0.95), 3),
        },
        "source_image_path": f"apps/api/tests/fixtures/extraction/diagram/{paper_id}/image.png",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "extraction_time_ms": round(rng.uniform(160, 460), 1),
        "fallback_used": False,
        "paper_id": paper_id,
    }


BUILDERS = {
    "plot": _build_plot,
    "table": _build_table,
    "microstructure": _build_micro,
    "diagram": _build_diagram,
}

# Deterministic per-type seed offsets (replaces non-deterministic hash(fig_type)).
_FIG_TYPE_SEED_OFFSET: dict[str, int] = {
    "plot": 0x3A7B,
    "table": 0x5C1F,
    "microstructure": 0x8E4D,
    "diagram": 0xA296,
}


def _paper_id(figure_type: str, idx: int) -> str:
    return f"{figure_type}-{idx:03d}"


def generate(
    output_dir: Path,
    counts: dict,
    seed: int,
    write_images: bool = True,
) -> list[Fixture]:
    rng = random.Random(seed)
    fixtures: list[Fixture] = []
    for fig_type, count in counts.items():
        if count <= 0:
            continue
        if fig_type not in BUILDERS:
            print(f"WARN: skipping unknown figure type {fig_type!r}", file=sys.stderr)
            continue
        builder = BUILDERS[fig_type]
        for idx in range(count):
            paper_id = _paper_id(fig_type, idx)
            fixture_dir = output_dir / fig_type / paper_id
            fixture_dir.mkdir(parents=True, exist_ok=True)

            if write_images:
                img_seed = (seed + idx * 7919 + _FIG_TYPE_SEED_OFFSET.get(fig_type, 0)) & 0xFFFFFFFF
                (fixture_dir / "image.png").write_bytes(
                    _synthetic_png(IMAGE_WIDTH, IMAGE_HEIGHT, img_seed, fig_type)
                )

            gt = builder(paper_id, idx, rng)
            (fixture_dir / "ground_truth.json").write_text(
                json.dumps(gt, indent=2) + "\n"
            )
            fixtures.append(Fixture(paper_id, fig_type, fixture_dir, gt))
    return fixtures


def _parse_counts(spec):
    out = dict(DEFAULT_COUNTS)
    for item in spec:
        if "=" not in item:
            raise SystemExit(f"--counts entries must be key=value (got {item!r})")
        key, _, val = item.partition("=")
        key = key.strip()
        if key not in DEFAULT_COUNTS:
            raise SystemExit(f"unknown figure type in --counts: {key}")
        try:
            out[key] = int(val)
        except ValueError as exc:
            raise SystemExit(f"invalid count for {key}: {val!r}") from exc
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", type=Path, required=True,
        help="Output root directory (e.g. apps/api/tests/fixtures/extraction).",
    )
    parser.add_argument(
        "--seed", type=int, default=20260101,
        help="Deterministic RNG seed (default 20260101).",
    )
    parser.add_argument(
        "--counts", action="append", default=[],
        help="Override per-type counts as plot=20,table=15,... (repeatable).",
    )
    parser.add_argument(
        "--skip-images", action="store_true",
        help="Do not write image.png (used by tests that only need JSON).",
    )
    args = parser.parse_args(argv)

    counts = _parse_counts(args.counts) if args.counts else DEFAULT_COUNTS
    args.output.mkdir(parents=True, exist_ok=True)

    fixtures = generate(
        args.output, counts, args.seed,
        write_images=not args.skip_images,
    )

    summary = {
        "seed": args.seed,
        "total": len(fixtures),
        "by_type": {t: counts.get(t, 0) for t in FIGURE_TYPES},
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
