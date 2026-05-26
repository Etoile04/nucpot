"""Entry point: run with `python -m verify_service`."""

import uvicorn
from verify_service.config import get_settings


def main():
    settings = get_settings()
    uvicorn.run(
        "verify_service.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=settings.PORT,
        reload=settings.DEBUG,
    )


if __name__ == "__main__":
    main()
