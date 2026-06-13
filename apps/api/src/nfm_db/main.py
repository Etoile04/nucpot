"""NFM-DB API application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nfm_db.api.v1 import (
    auth_endpoints,
    extraction,
    feedback,
    health,
    reference_gaps,
    reference_values,
    verification,
    viz,
)

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

app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(feedback.router, prefix="/api/v1", tags=["feedback"])
app.include_router(reference_values.router, prefix="/api/v1", tags=["reference-values"])
app.include_router(reference_gaps.router, prefix="/api/v1", tags=["reference-gaps"])
app.include_router(extraction.router, prefix="/api/v1", tags=["extraction"])
app.include_router(viz.router, prefix="/api/v1", tags=["visualization"])
app.include_router(verification.router, prefix="/api/v1/verification", tags=["verification"])
app.include_router(auth_endpoints.router, prefix="/api/v1", tags=["authentication"])
