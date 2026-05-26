"""Database layer for NucPot Verification Service.

Uses psycopg (v3) directly — lightweight, no ORM overhead for MVP.
Can be swapped to Supabase client later.
"""

from __future__ import annotations

import os
from typing import Optional

import psycopg

from config import settings

# Module-level connection cache
_conn: Optional[psycopg.Connection] = None


def _get_conn() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(settings.DATABASE_URL, autocommit=True)
    return _conn


def ensure_tables():
    """Create verifications and reference_values tables if they don't exist."""
    conn = _get_conn()
    migration_path = os.path.join(
        os.path.dirname(__file__), "..", "supabase", "migrations", "002_verifications.sql"
    )
    if os.path.exists(migration_path):
        sql = open(migration_path).read()
        conn.execute(sql)


def _serialize_row(row: dict) -> dict:
    """Convert DB row values to JSON-safe types."""
    import uuid
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, uuid.UUID):
            d[k] = str(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


# ---------------------------------------------------------------------------
# Potentials
# ---------------------------------------------------------------------------

def get_potential(potential_id: str) -> Optional[dict]:
    conn = _get_conn()
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM potentials WHERE id = %s", (potential_id,))
        row = cur.fetchone()
        return _serialize_row(row) if row else None


# ---------------------------------------------------------------------------
# Verifications
# ---------------------------------------------------------------------------

def insert_verification(vid: str, potential_id: str, status: str = "pending",
                        requested_by: str | None = None):
    conn = _get_conn()
    conn.execute(
        """INSERT INTO verifications (id, potential_id, status, requested_by)
           VALUES (%s, %s, %s, %s)""",
        (vid, potential_id, status, requested_by),
    )


def update_verification(
    vid: str,
    *,
    status: str | None = None,
    results: dict | None = None,
    overall_grade: str | None = None,
    summary: str | None = None,
    error_log: str | None = None,
    compute_time: int | None = None,
):
    conn = _get_conn()
    parts: list[str] = []
    params: list = []
    if status:
        parts.append("status = %s")
        params.append(status)
    if results is not None:
        import json
        parts.append("results = %s")
        params.append(json.dumps(results))
    if overall_grade:
        parts.append("overall_grade = %s")
        params.append(overall_grade)
    if summary:
        parts.append("summary = %s")
        params.append(summary)
    if error_log:
        parts.append("error_log = %s")
        params.append(error_log)
    if compute_time is not None:
        parts.append("compute_time = %s")
        params.append(compute_time)
    if status in ("completed", "failed"):
        parts.append("completed_at = NOW()")

    if parts:
        params.append(vid)
        conn.execute(
            f"UPDATE verifications SET {', '.join(parts)} WHERE id = %s", params
        )


def get_verification(vid: str) -> Optional[dict]:
    conn = _get_conn()
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM verifications WHERE id = %s", (vid,))
        row = cur.fetchone()
        return _serialize_row(row) if row else None


def get_latest_verification(potential_id: str) -> Optional[dict]:
    conn = _get_conn()
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            """SELECT * FROM verifications
               WHERE potential_id = %s
               ORDER BY created_at DESC LIMIT 1""",
            (potential_id,),
        )
        row = cur.fetchone()
        return _serialize_row(row) if row else None


# ---------------------------------------------------------------------------
# Reference Values
# ---------------------------------------------------------------------------

def get_reference_values(element_system: str) -> list[dict]:
    conn = _get_conn()
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT * FROM reference_values WHERE element_system = %s",
            (element_system,),
        )
        return [dict(r) for r in cur.fetchall()]
