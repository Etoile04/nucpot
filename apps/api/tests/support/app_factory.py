"""Cluster-facing access to the app factory (NFM-5951 core layer).

C2 step 3 gate: cluster test trees must not reference ``nfm_db.main``
(``rg 'nfm_db\\.main' tests/<cluster>/`` zero hits).  This module is the
single sanctioned indirection point — clusters import ``create_app`` and
the mount list from here, keeping the ``nfm_db.main`` dependency
centralized in the support layer.
"""

from __future__ import annotations

from nfm_db.main import (
    ALL_ROUTER_MOUNTS,
    HandlerInstaller,
    MiddlewareSpec,
    RouterMount,
    create_app,
    install_standard_handlers,
)

__all__ = [
    "ALL_ROUTER_MOUNTS",
    "HandlerInstaller",
    "MiddlewareSpec",
    "RouterMount",
    "create_app",
    "install_standard_handlers",
]
