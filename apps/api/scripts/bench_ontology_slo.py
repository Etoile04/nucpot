"""SLO benchmark for the ontology NVL derivation (T7).

Measures p50/p95/p99 latency of ``derive_ontology_graph`` over a synthetic
<5k-node corpus in an in-memory SQLite database, and prints PASS/FAIL against
the NFM-266 SLO target (p95 < 500 ms).

Run (from apps/api):

    python scripts/bench_ontology_slo.py

This is an evidence-gathering script, NOT a CI gate — benchmark numbers are
environment-dependent. The recorded number is posted as T7 evidence.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.models import Base
from nfm_db.models.ref_gap_fill import Confidence, RefGapFillStaging, StagingStatus
from nfm_db.services.ontology_service import derive_ontology_graph

# Synthetic corpus ~ 4k nodes (2000 materials x (mat+prop) + 2 methods + 1 src).
MATERIAL_COUNT = 2000
CORPUS_ID = "slo-bench"
TARGET_P95_MS = 500
ITERATIONS = 60
WARMUP = 5


async def _seed(session: AsyncSession) -> None:
    rows = []
    for i in range(MATERIAL_COUNT):
        method = "DFT" if i % 2 == 0 else "EXP"
        rows.append(
            RefGapFillStaging(
                element_system=f"M{i}",
                property_name=f"p{i}",
                value=float(i),
                unit="unit",
                source=CORPUS_ID,
                method=method,
                confidence=Confidence.MEDIUM,
                dedup_hash=f"{CORPUS_ID}:{i}",
                range_validated=True,
                status=StagingStatus.PENDING,
            )
        )
    session.add_all(rows)
    await session.commit()


def _percentile(sorted_samples: list[float], pct: float) -> float:
    if not sorted_samples:
        return 0.0
    k = (len(sorted_samples) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_samples) - 1)
    return sorted_samples[lo] + (sorted_samples[hi] - sorted_samples[lo]) * (k - lo)


async def main() -> int:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await _seed(session)
        node_count = len((await derive_ontology_graph(session, CORPUS_ID)).nodes)

        for _ in range(WARMUP):
            await derive_ontology_graph(session, CORPUS_ID)

        samples_ms: list[float] = []
        for _ in range(ITERATIONS):
            start = time.perf_counter()
            await derive_ontology_graph(session, CORPUS_ID)
            samples_ms.append((time.perf_counter() - start) * 1000)

    samples_ms.sort()
    p50 = _percentile(samples_ms, 0.50)
    p95 = _percentile(samples_ms, 0.95)
    p99 = _percentile(samples_ms, 0.99)
    mean = statistics.mean(samples_ms)

    print(f"corpus nodes           : {node_count}")
    print(f"iterations             : {ITERATIONS} (warmup {WARMUP})")
    print(f"mean                   : {mean:.2f} ms")
    print(f"p50                    : {p50:.2f} ms")
    print(f"p95                    : {p95:.2f} ms")
    print(f"p99                    : {p99:.2f} ms")
    print(f"SLO target p95         : < {TARGET_P95_MS} ms")
    verdict = "PASS" if p95 < TARGET_P95_MS else "FAIL"
    print(f"verdict                : {verdict}")
    return 0 if p95 < TARGET_P95_MS else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
