"""NFM-DB API application entry point."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.types import ASGIApp, Receive, Scope, Send

from nfm_db.api import admin as admin_api
from nfm_db.api.routes.ontology import ontology_router as register_router
from nfm_db.api.v1 import (
    blog,
    composition,
    conflict,
    data_collection,
    dedup,
    design,
    dft,
    extraction,
    extraction_gaps,
    feedback,
    health,
    kg,
    kg_graph,
    lightrag,
    literature,
    materials,
    md_verification,
    ontology,
    ontology_version,
    potentials,
    prediction,
    properties,
    re_extraction,
    reference_gaps,
    reference_values,
    review,
    seed,
    sources,
    upload,
    verification,
    viz,
)
from nfm_db.api.v1.auth_endpoints import router as auth_endpoints
from nfm_db.api.v1.batch import (
    materials_router as batch_materials_router,
)
from nfm_db.api.v1.batch import (
    properties_router as batch_properties_router,
)
from nfm_db.api.v1.batch import (
    reference_values_router as batch_reference_values_router,
)
from nfm_db.api.v1.hub_nodes import router as hub_nodes_router
from nfm_db.api.v1.profile import (
    contributions_router,
    profile_router,
    stats_router,
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
from nfm_db.services.lightrag_lifecycle import close_lightrag_client
from nfm_db.services.upload_service import PotentialUploadError


class ProxyHeadersMiddleware:
    """Re-implementation of Starlette's removed ProxyHeadersMiddleware.

    Trust the Cloudflare Tunnel proxy chain so ``request.client.host``
    reflects the real visitor IP (CF edge) instead of ``127.0.0.1``
    (local tunnel). Without this, slowapi's per-IP rate-limit
    collides on the single 127.0.0.1 socket and a single misbehaving
    visitor locks out every other user behind the tunnel for a full
    minute. See NFM-2087.

    In our deployment the API container is *only* reachable through the
    Cloudflare Tunnel (no public bind), so we can safely trust all
    upstream hosts (X-Forwarded-For from CF edge).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Decode the headers once (ASGI passes them as bytes).
        headers = {
            k.decode("latin-1", errors="ignore").lower(): v.decode("latin-1", errors="ignore")
            for k, v in (scope.get("headers") or [])
        }
        xff = headers.get("x-forwarded-for", "")
        xfp = headers.get("x-forwarded-proto", "")
        xfh = headers.get("x-forwarded-host", "")

        # Pick the original client (left-most XFF entry).
        if xff:
            client_ip = xff.split(",")[0].strip() or (scope.get("client") or ("127.0.0.1", 0))[0]
        else:
            client_ip = (scope.get("client") or ("127.0.0.1", 0))[0]

        # Patch the scope so downstream ASGI apps see the real client /
        # scheme / host. Starlette's request.url_for() and slowapi's
        # ``get_remote_address`` both read from ``scope``.
        new_scope = dict(scope)
        new_scope["client"] = (client_ip, (scope.get("client") or ("", 0))[1])
        if xfp:
            new_scope["scheme"] = xfp
        if xfh:
            new_scope["server"] = (xfh, new_scope.get("server", ("", 0))[1])

        await self.app(new_scope, receive, send)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage shared-resource lifecycle (NFM-1245 / NFM-1222 HIGH-2).

    The shared ``httpx.AsyncClient`` used by all LightRAG consumers
    (API endpoints, fire-and-forget tasks, RAG provider) is created
    lazily on first use and closed on shutdown via
    ``close_lightrag_client()``. The close helper swallows any close
    errors so this await is always safe.
    """
    yield
    await close_lightrag_client()


app = FastAPI(
    title="核燃料与材料物性数据库 API",
    description=(
        "## 核燃料与材料物性数据库 (Nuclear Fuel & Materials Properties Database, NFMD)\n\n"
        "面向核燃料与材料研究的物性数据管理与知识服务平台。\n\n"
        "### 主要功能模块\n\n"
        "- **材料管理 (Materials)** — 核素、化合物、合金等材料的元数据维护\n"
        "- **物性数据 (Properties)** — 实验测量与计算模拟的物性数据,支持条件筛选与统计分析\n"
        "- **势函数 (Potentials)** — 分子动力学势函数元数据与文件上传\n"
        "- **数据源 (Sources)** — 文献、数据库等参考数据源管理\n"
        "- **参考值 (Reference Values)** — 参考数据批量入库、质量门控、待审核与发布\n"
        "- **参考值缺口 (Reference Gaps)** — 覆盖率统计、缺口扫描与填充触发\n"
        "- **信息抽取 (Extraction)** — OntoFuel 文献信息抽取流水线\n"
        "- **本体管理 (Ontology)** — NFMD 本体的 NVL 图与图查询 (节点/邻居/最短路径)\n"
        "- **可视化 (Visualization)** — 本体统计与 NVL 可视化数据\n"
        "- **知识图谱 (Knowledge Graph)** — 知识图谱节点搜索与查询\n"
        "- **MD 验证 (MD Verification)** — 分子动力学验证作业管理 (提交/查询/取消/结果)\n"
        "- **领域专家审核 (Verification)** — 参考值校验、F 级裁决、季度审计\n"
        "- **冲突解决 (Conflicts)** — 多源数据冲突的列示与解决策略\n"
        "- **审核流程 (Review)** — 抽取结果、知识图谱、物性数据的跨表审核与溯源\n"
        "- **文献管理 (Literature)** — PDF 上传、解析与提取管理\n"
        "- **LightRAG** — LightRAG 侧车文档检索与图查询\n"
        "- **认证 (Authentication)** — 用户登录、注册、角色管理\n"
        "- **反馈 (Feedback)** — 用户反馈提交与后台审阅\n"
        "- **博客 (Blog)** — 内部博客管理 (草稿/审核/发布工作流)\n"
        "- **健康检查 (Health)** — 服务可用性与模块健康状态\n"
        "- **ML预测 (ML Prediction)** — 相类型分类与相变温度预测\n"
        "- **设计优化 (Design Optimization)** — NSGA-II多目标合金成分优化\n\n"
        "### API 约定\n\n"
        "所有响应均使用统一的 `{success: bool, data?: ..., error?: string}` 信封结构。\n"
        "分页接口返回 `{items, total, page, limit, pages}` 元数据。\n"
        "请求与响应均遵循 RFC 7807 (Problem Details for HTTP APIs) 风格,错误字段使用 `detail`。"
    ),
    version="0.1.0",
    openapi_tags=[
        {"name": "材料管理", "description": "核燃料与材料的元数据管理(CRUD、搜索、分类)。"},
        {
            "name": "物性数据",
            "description": "实验与计算物性测量数据,支持按材料/属性类型/时间筛选与统计分析。",
        },
        {"name": "势函数", "description": "分子动力学势函数元数据与文件附件管理。"},
        {"name": "数据源", "description": "参考数据源(期刊、数据库、报告)的元数据与作者管理。"},
        {
            "name": "参考值",
            "description": "参考值批量入库、质量门控、审核队列、批准/驳回、外部校验回调。",
        },
        {"name": "参考值缺口", "description": "覆盖率统计、缺口扫描与填充触发。"},
        {"name": "信息抽取", "description": "OntoFuel 文献信息抽取流水线(触发、状态查询)。"},
        {
            "name": "本体管理",
            "description": "NFMD 本体的 NVL 图、节点邻居查询、模糊搜索、最短路径与同步。",
        },
        {"name": "可视化", "description": "本体统计与 NVL 可视化数据接口。"},
        {"name": "知识图谱", "description": "知识图谱节点的检索与查询。"},
        {
            "name": "MD 验证",
            "description": "分子动力学验证作业的提交、列表、详情、状态、取消、模拟/缺陷/拟合结果。",
        },
        {
            "name": "领域专家审核",
            "description": "领域专家审核工作流:参考值校验、F 级裁决、季度审计。",
        },
        {"name": "冲突解决", "description": "多源数据冲突的列示与自动/手动解决策略。"},
        {"name": "审核流程", "description": "跨表的待审条目、溯源、状态更新、批量操作与统计。"},
        {"name": "文献管理", "description": "PDF 上传、解析与提取、检索与删除。"},
        {"name": "LightRAG", "description": "LightRAG 侧车文档检索、查询与图遍历。"},
        {"name": "认证", "description": "用户登录、注册、当前用户信息、角色分配。"},
        {"name": "反馈", "description": "用户反馈提交与后台审阅。"},
        {"name": "博客", "description": "内部博客的 CRUD 与审核/发布工作流(管理员/编辑)。"},
        {"name": "健康检查", "description": "服务与各模块的健康状态。"},
        {"name": "V4 信息抽取", "description": "NFM v4 版本的信息抽取接口(实验性)。"},
        {"name": "ML预测", "description": "ML相类型分类与相变温度预测。"},
        {
            "name": "设计优化",
            "description": "NSGA-II多目标合金成分优化设计与帕累托前沿搜索。",
        },
        {
            "name": "实体去重",
            "description": (
                "材料去重引擎：扫描重复候选、记录合并决策、查询审计日志 "  # noqa: RUF001
                "(NFM-1391, B3.1.1)。"
            ),
        },
        {
            "name": "DFT 计算",
            "description": (
                "密度泛函计算记录管理：列表、详情、JSON/CSV 批量导入、"  # noqa: RUF001
                "聚合统计 (NFM-1678)。"
            ),
        },
    ],
    lifespan=lifespan,
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

# Trust the Cloudflare Tunnel proxy chain (X-Forwarded-For from CF
# edge) so request.client.host reflects the real visitor IP. Without
# this slowapi collides on the single 127.0.0.1 socket — see
# ProxyHeadersMiddleware class above. NFM-2087.
app.add_middleware(ProxyHeadersMiddleware)

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


app.include_router(health.router, prefix="/api/v1", tags=["健康检查"])
app.include_router(admin_api.router, prefix="/api/admin", tags=["管理健康监控"])
app.include_router(feedback.router, prefix="/api/v1", tags=["反馈"])
app.include_router(reference_values.router, prefix="/api/v1", tags=["参考值"])
app.include_router(reference_gaps.router, prefix="/api/v1", tags=["参考值缺口"])
app.include_router(extraction.router, prefix="/api/v1", tags=["信息抽取"])
app.include_router(extraction_gaps.router, prefix="/api/v1", tags=["提取缺口管理"])
app.include_router(viz.router, prefix="/api/v1", tags=["可视化"])
app.include_router(ontology.router, prefix="/api/v1", tags=["本体管理"])
app.include_router(ontology_version.router, prefix="/api/v1", tags=["本体版本管理"])
# NFM-3591 — register-version endpoint with SHA-256 source validation.
# Mounted at /api (no /v1 prefix) per the issue's API spec.
app.include_router(register_router, prefix="/api", tags=["本体版本注册"])
app.include_router(data_collection.router, prefix="/api/v1", tags=["数据采集管理"])
app.include_router(re_extraction.router, prefix="/api/v1", tags=["重新提取管理"])
app.include_router(verification.router, prefix="/api/v1/verification", tags=["领域专家审核"])
app.include_router(md_verification.router, prefix="/api/v1/md-verification", tags=["MD 验证"])
app.include_router(auth_endpoints, prefix="/api/v1", tags=["认证"])
app.include_router(profile_router, prefix="/api/v1", tags=["用户资料"])
app.include_router(contributions_router, prefix="/api/v1", tags=["贡献"])
app.include_router(stats_router, prefix="/api/v1", tags=["统计"])
app.include_router(blog.router, prefix="/api/v1", tags=["博客"])
app.include_router(potentials.router, prefix="/api/v1", tags=["势函数"])
app.include_router(batch_materials_router, prefix="/api/v1", tags=["批量材料"])
app.include_router(batch_properties_router, prefix="/api/v1", tags=["批量物性"])
app.include_router(materials.router, prefix="/api/v1", tags=["材料管理"])
app.include_router(properties.router, prefix="/api/v1", tags=["物性数据"])
app.include_router(sources.router, prefix="/api/v1", tags=["数据源"])
app.include_router(seed.router, prefix="/api/v1", tags=["种子数据"])
app.include_router(kg.router, prefix="/api/v1", tags=["知识图谱"])
app.include_router(kg_graph.router, prefix="/api/v1", tags=["知识图谱"])
app.include_router(review.router, prefix="/api/v1", tags=["审核流程"])
app.include_router(conflict.router, prefix="/api/v1", tags=["冲突解决"])
app.include_router(literature.router, prefix="/api/v1", tags=["文献管理"])
app.include_router(lightrag.router, prefix="/api/v1/lightrag", tags=["LightRAG"])
app.include_router(v4_extraction.router, prefix="/api/v4", tags=["V4 信息抽取"])
app.include_router(batch_reference_values_router, prefix="/api/v1", tags=["批量参考值"])
app.include_router(prediction.router, prefix="/api/v1", tags=["ML预测"])
app.include_router(design.router, prefix="/api/v1", tags=["设计优化"])
app.include_router(upload.router, prefix="/api/v1", tags=["断点续传"])
app.include_router(composition.router, prefix="/api/v1", tags=["成分设计"])
app.include_router(dedup.router, prefix="/api/v1", tags=["实体去重"])
app.include_router(dft.router, prefix="/api/v1", tags=["DFT 计算"])
app.include_router(
    hub_nodes_router, prefix="/api/v1", tags=["Hub 节点管理"]
)
