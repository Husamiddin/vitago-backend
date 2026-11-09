from contextlib import asynccontextmanager
import logging
import traceback
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.admin_routes import router as admin_router
from app.api.analytics_routes import router as analytics_router
from app.api.auth_routes import router as auth_router
from app.api.feature_routes import router as feature_router
from app.api.notification_routes import router as notification_router
from app.api.plan_routes import router as plan_router
from app.api.routes import router as api_router
from app.core.config import settings
from app.core.security import SecurityAndRateLimitMiddleware
from app.db.database import ensure_db_initialized

logger = logging.getLogger("tetik.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_db_initialized()
    yield
    try:
        from app.db.database import engine
        engine.dispose()
    except Exception as e:
        logger.warning(f"Error disposing DB engine on shutdown: {e}")


app = FastAPI(
    title="Tetik API",
    description="AI Retsept Skaneri, Sog'lom Hayot va Sport Platformasi",
    version=settings.VERSION,
    lifespan=lifespan,
)


# Global Exception Handlers for Robust Stability & Defense
@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "error": True,
            "detail": exc.detail,
            "status_code": exc.status_code,
            "reply": str(exc.detail),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "error": True,
            "detail": "Kiritilgan ma'lumotlar formati noto'g'ri.",
            "errors": exc.errors(),
            "status_code": 422,
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):
    logger.error(f"Unhandled server exception: {exc}\n{traceback.format_exc()}")
    # Never leak internal database passwords or stack traces to end users
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": True,
            "detail": "Serverda vaqtinchalik xatolik yuz berdi. Iltimos qaytadan urinib ko'ring.",
            "reply": "Tetik AI tizimi so'rovingizni qayta ishlamoqda. Iltimos, savolingizni yana bir bor yuboring.",
            "status_code": 500,
        },
    )


# Middlewares (Security Headers, Rate Limiting, CORS)
app.add_middleware(SecurityAndRateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True if settings.CORS_ORIGINS != ["*"] else False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(auth_router)
app.include_router(plan_router)
app.include_router(feature_router)
app.include_router(analytics_router)
app.include_router(notification_router)
app.include_router(admin_router)


@app.get("/health")
def health_check():
    from app.db.database import get_db_pool_status

    pool_metrics = get_db_pool_status()
    return {
        "status": "ok",
        "service": "Tetik API",
        "model": settings.GEMINI_MODEL,
        "security": "active",
        "rate_limit": f"{settings.RATE_LIMIT_PER_MINUTE}/min",
        "database": {
            "health": pool_metrics.get("health", "unknown"),
            "ping_latency_ms": pool_metrics.get("ping_latency_ms"),
            "dialect": pool_metrics.get("engine_dialect"),
        },
    }
