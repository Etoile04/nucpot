"""E2E integration tests for the 1+N distributed architecture.

Simulates multi-node topologies (1 hub + N resource nodes) with
network partition scenarios, offline/reconnect sync, and conflict
resolution using real FastAPI + SQLite + vector clock components.
"""
