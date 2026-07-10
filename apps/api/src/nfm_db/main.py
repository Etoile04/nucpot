"""NFM-DB API application entry point."""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from nfm_db.api.v1 import (
    auth_endpoints,
    blog,
    conflict,
    extraction,
    feedback,
    health,
    kg,
    lightrag,
    literature,
    materials,
    md_verification,
    ontology,
    potentials,
    properties,
    reference_gaps,
    reference_values,
    review,
    sources,
    verification,
    viz,
)
from nfm_db.api.v4 import extraction as v4_extraction
from nfm_db.middleware.rate_limit import (
    NFMRateLimitMiddleware,
    limiter,
    rate_limit_exceeded_handler,
)
from nfm_db.schemas.errors import (
    ErrorCode,
    register_http_exception_handler,
)
from nfm_db.services.upload_service import PotentialUploadError

app = FastAPI(
    title="核燃料与材料物性数据库 API",
    description="Nuclear Fuel & Materials Properties Database API",
    version="0.1.0",
    tags_metadata=[
        {
            "name": "health",
            "description": "健康检查 — API 服务状态监控",
        },
        {
            "name": "materials",
            "description": "材料管理 — 核燃料材料的增删改查",
        },
        {
            "name": "properties",
            "description": "物性数据 — 材料性能测量数据管理",
        },
        {
            "name": "sources",
            "description": "数据来源 — 文献与数据源管理",
        },
        {
            "name": "feedback",
            "description": "用户反馈 — 意见收集与管理",
        },
        {
            "name": "extraction",
            "description": "数据提取 — 从文献中自动提取物性数据",
        },
        {
            "name": "ontology",
            "description": "本体管理 — 核材料领域知识本体与图查询",
        },
        {
            "name": "visualization",
            "description": "可视化 — NVL 图与本体统计可视化",
        },
        {
            "name": "verification",
            "description": "数据验证 — 参考数据验证与审计",
        },
        {
            "name": "md-verification",
            "description": "MD验证 — 分子动力学模拟验证工作流",
        },
        {
            "name": "authentication",
            "description": "用户认证 — 登录注册与权限管理",
        },
        {
            "name": "blog",
            "description": "博客 — 技术文章发布与工作流管理",
        },
        {
            "name": "potentials",
            "description": "势函数 — 原子间势函数文件管理",
        },
        {
            "name": "reference-values",
            "description": "参考值 — 参考数据暂存与审核",
        },
        {
            "name": "reference-gaps",
            "description": "参考间隙 — 参考数据缺失扫描与填补",
        },
        {
            "name": "knowledge-graph",
            "description": "知识图谱 — 知识图谱构建、查询与审核",
        },
        {
            "name": "review",
            "description": "人工审核 — 提取数据人工审核与批处理",
        },
        {
            "name": "conflicts",
            "description": "冲突解决 — 数据冲突检测与解决策略",
        },
        {
            "name": "literature",
            "description": "文献管理 — PDF 上传、解析与提取管理",
        },
        {
            "name": "lightrag",
            "description": "LightRAG — LightRAG 侧车文档检索与图查询",
        },
        {
            "name": "v4-extraction",
            "description": "V4数据提取 — 增强版多模态提取流水线",
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

# Global rate limiting (NFM-1087): per-IP limits via slowapi on all /api/ routes.
# Health endpoint is exempt. Configurable via RATE_LIMIT_* env vars.
app.state.limiter = limiter
app.exception_handlers[RateLimitExceeded] = rate_limit_exceeded_handler
app.add_middleware(NFMRateLimitMiddleware)

# Global HTTP exception handler (NFM-1090): enriches all HTTPException
# responses with standard error_code and Chinese message.
register_http_exception_handler(app)


# Map PotentialUploadError status codes to ErrorCode for the standard envelope.
_STATUS_TO_UPLOAD_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.BAD_REQUEST,
    415: ErrorCode.BAD_REQUEST,
}


@app.exception_handler(PotentialUploadError)
async def _upload_error_handler(_request: Request, exc: PotentialUploadError) -> JSONResponse:
    """Return upload errors in the standard error envelope (NFM-1090)."""
    error_code = _STATUS_TO_UPLOAD_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error_code": error_code.value,
            "error": exc.message,
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
app.include_router(kg.router, prefix="/api/v1", tags=["knowledge-graph"])
app.include_router(review.router, prefix="/api/v1/review", tags=["review"])
app.include_router(conflict.router, prefix="/api/v1/conflicts", tags=["conflicts"])
app.include_router(literature.router, prefix="/api/v1/literature", tags=["literature"])
app.include_router(lightrag.router, prefix="/api/v1/lightrag", tags=["lightrag"])
app.include_router(v4_extraction.router, prefix="/api/v4", tags=["v4-extraction"])
