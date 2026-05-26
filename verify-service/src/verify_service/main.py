"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from verify_service.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="NucPot Verify Service",
        version="0.1.0",
        description="Automated verification for nuclear material interatomic potentials",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api")
    return app
