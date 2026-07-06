"""NFM-DB API application entry point."""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from nfm_db.api.v1 import (
    blog,
    extraction,
    feedback,
    health,
    md_verification,
    ontology,
    potentials,
    reference_gaps,
    reference_values,
    verification,
    viz,
)
from nfm_db.services.upload_service import PotentialUploadError

# Optional v1 modules (may not exist in all branches yet)
try:
    from nfm_db.api.v1 import materials as materials_mod
except ImportError:
    materials_mod = None  # type: ignore[assignment]

try:
    from nfm_db.api.v1 import properties as properties_mod
except ImportError:
    properties_mod = None  # type: ignore[assignment]

try:
    from nfm_db.api.v1 import seed as seed_mod
except ImportError:
    seed_mod = None  # type: ignore[assignment]

try:
    from nfm_db.api.v1 import sources as sources_mod
except ImportError:
    sources_mod = None  # type: ignore[assignment]

try:
    from nfm_db.api.v1 import auth_endpoints
except ImportError:
    auth_endpoints = None  # type: ignore[assignment]

try:
    from nfm_db.api.v4 import extraction as v4_extraction
except ImportError:
    v4_extraction = None  # type: ignore[assignment]

app = FastAPI(
    title="核燃料与材料物性数据库 API",
    description="Nuclear Fuel & Materials Properties Database API",
    version="0.1.0",
)

# CORS origins: env var (comma-separated) or sensible defaults.
# Production: set CORS_ORIGINS=https://nucpot.dpdns.org,https://yourdomain.com
_cors_env = os.environ.get("CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env
    else [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://nucpot.dpdns.org",
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PotentialUploadError)
async def _upload_error_handler(_request: Request, exc: PotentialUploadError) -> JSONResponse:
    """Return upload errors in the ApiResponse envelope for consistency."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.message},
    )


app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(feedback.router, prefix="/api/v1", tags=["feedback"])
app.include_router(reference_values.router, prefix="/api/v1", tags=["reference-values"])
app.include_router(reference_gaps.router, prefix="/api/v1", tags=["reference-gaps"])
app.include_router(extraction.router, prefix="/api/v1", tags=["extraction"])
app.include_router(viz.router, prefix="/api/v1", tags=["visualization"])
app.include_router(ontology.router, prefix="/api/v1", tags=["ontology"])
app.include_router(verification.router, prefix="/api/v1/verification", tags=["verification"])
app.include_router(md_verification.router, prefix="/api/v1/md-verification", tags=["md-verification"])
if auth_endpoints is not None:
    app.include_router(auth_endpoints.router, prefix="/api/v1", tags=["authentication"])
app.include_router(blog.router, prefix="/api/v1", tags=["blog"])
app.include_router(potentials.router, prefix="/api/v1", tags=["potentials"])
if sources_mod is not None:
    app.include_router(sources_mod.router, prefix="/api/v1", tags=["sources"])
if materials_mod is not None:
    app.include_router(materials_mod.router, prefix="/api/v1", tags=["materials"])
if properties_mod is not None:
    app.include_router(properties_mod.router, prefix="/api/v1", tags=["properties"])
if seed_mod is not None:
    app.include_router(seed_mod.router, prefix="/api/v1", tags=["seed"])
if v4_extraction is not None:
    app.include_router(v4_extraction.router, prefix="/api/v4", tags=["v4-extraction"])
