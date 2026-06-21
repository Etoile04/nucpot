"""NFM-DB API application entry point."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from nfm_db.api.v1 import (
    auth_endpoints,
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

app = FastAPI(
    title="核燃料与材料物性数据库 API",
    description="Nuclear Fuel & Materials Properties Database API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
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
app.include_router(auth_endpoints.router, prefix="/api/v1", tags=["authentication"])
app.include_router(blog.router, prefix="/api/v1", tags=["blog"])
app.include_router(potentials.router, prefix="/api/v1", tags=["potentials"])
