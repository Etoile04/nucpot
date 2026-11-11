"""Core test-support layer (NFM-5951 C2 step 1).

Three-layer test assembly:

1. **Core layer** (this package) — environment pins, SQLite-compat schema
   helpers, db/pg session primitives, role fixtures. Reusable by any cluster
   conftest without touching the app singleton.
2. **App factory** — ``nfm_db.main.create_app`` (step 2); cluster-level
   clients build router subsets instead of importing the module-level app.
3. **Clusters** — per-directory conftests re-exporting the core fixtures and
   mounting only the routers they need (step 3 pilot: ``tests/providers/``).

The root ``tests/conftest.py`` re-exports this package's fixtures so the 432
flat test files keep their current import surface unchanged.
"""
