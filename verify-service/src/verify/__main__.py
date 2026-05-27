"""Run the verification service with uvicorn."""

import uvicorn


def main():
    uvicorn.run(
        "verify.api.routes:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
