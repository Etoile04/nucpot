"""NFM-DB API application entry point."""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from nfm_db.api.v1 import (
    auth_endpoints,
    blog,
    extraction,
    feedback,
    health,
    materials,
    md_verification,
    ontology,
    potentials,
    properties,
    reference_gaps,
    reference_values,
    sources,
    verification,
    viz,
)
from nfm_db.api.v4 import extraction as v4_extraction
from nfm_db.schemas.common import ErrorCode
from nfm_db.services.upload_service import PotentialUploadError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP status code → ErrorCode mapping
# ---------------------------------------------------------------------------

_STATUS_TO_ERROR_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.AUTHENTICATION_ERROR,
    403: ErrorCode.PERMISSION_ERROR,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.VALIDATION_ERROR,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMIT_EXCEEDED,
}


def _status_to_error_code(status_code: int) -> ErrorCode:
    """Map an HTTP status code to the closest ErrorCode."""
    return _STATUS_TO_ERROR_CODE.get(status_code, ErrorCode.INTERNAL_ERROR)

app = FastAPI(
    title="核燃料与材料物性数据库 API",
    description="Nuclear Fuel & Materials Properties Database API",
    version="0.1.0",
    tags_metadata=[
        {
            "name": "health",
            "description": "健康检查 — 服务运行状态与就绪探针",
        },
        {
            "name": "materials",
            "description": "材料管理 — 核燃料材料的增删改查与搜索",
        },
        {
            "name": "properties",
            "description": "物性数据 — 材料物理化学属性数据管理",
        },
        {
            "name": "sources",
            "description": "数据来源 — 文献与数据源信息管理",
        },
        {
            "name": "feedback",
            "description": "用户反馈 — 用户意见提交与管理员查看",
        },
        {
            "name": "extraction",
            "description": "数据提取 — V1 从文献中提取结构化数据",
        },
        {
            "name": "ontology",
            "description": "本体管理 — NucMat 核材料领域本体",
        },
        {
            "name": "visualization",
            "description": "可视化 — NVL 图谱与统计数据可视化",
        },
        {
            "name": "verification",
            "description": "数据验证 — 物性数据自动验证与评分",
        },
        {
            "name": "md-verification",
            "description": "MD验证 — 分子动力学模拟结果验证",
        },
        {
            "name": "authentication",
            "description": "用户认证 — 登录、注册与角色管理",
        },
        {
            "name": "blog",
            "description": "博客 — 技术文章发布与管理",
        },
        {
            "name": "potentials",
            "description": "势函数 — 原子间势函数文件管理",
        },
        {
            "name": "reference-values",
            "description": "参考值 — 参考数据值提交、审核与导出",
        },
        {
            "name": "reference-gaps",
            "description": "参考间隙 — 参考数据空白检测与填补",
        },
        {
            "name": "literature",
            "description": "文献管理 — 文献上传、解析、提取与检索",
        },
        {
            "name": "v4-extraction",
            "description": "V4数据提取 — 多模态文献智能提取引擎",
        },
    ],
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
        content={
            "success": False,
            "error": exc.message,
            "detail": exc.message,
            "error_code": _status_to_error_code(exc.status_code),
        },
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Pydantic validation errors → VALIDATION_ERROR with details."""
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": "Validation error",
            "detail": "Validation error",
            "error_code": ErrorCode.VALIDATION_ERROR,
            "details": {"errors": exc.errors()},
        },
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(
    _request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """FastAPI HTTPException → ErrorResponse with appropriate ErrorCode."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
            "detail": exc.detail,
            "error_code": _status_to_error_code(exc.status_code),
        },
    )


@app.exception_handler(Exception)
async def _global_exception_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    """Catch-all for unhandled exceptions → INTERNAL_ERROR."""
    logger.exception("Unhandled exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "detail": "Internal server error",
            "error_code": ErrorCode.INTERNAL_ERROR,
        },
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
app.include_router(materials.router, prefix="/api/v1", tags=["materials"])
app.include_router(properties.router, prefix="/api/v1", tags=["properties"])
app.include_router(sources.router, prefix="/api/v1", tags=["sources"])
app.include_router(v4_extraction.router, prefix="/api/v4", tags=["v4-extraction"])
