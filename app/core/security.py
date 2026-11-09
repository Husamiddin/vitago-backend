import logging
import os
import threading
import time
from typing import Dict, List, Tuple
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import settings

logger = logging.getLogger("tetik.security")


class RateLimiter:
    """Sliding-window in-memory rate limiter per client IP."""

    def __init__(self):
        # Maps IP -> list of request timestamps (float)
        self._requests: Dict[str, List[float]] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.time()

    def _get_client_ip(self, request: Request) -> str:
        # Check standard reverse proxy headers
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # First IP in comma-separated list is the client IP
            return forwarded.split(",")[0].strip()
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        return request.client.host if request.client else "127.0.0.1"

    def is_rate_limited(self, request: Request) -> Tuple[bool, int, int]:
        """
        Returns (is_limited, current_count, max_limit).
        """
        path = request.url.path
        # Exclude static assets or health checks
        if path in ("/health", "/favicon.ico") or path.startswith(("/assets", "/static")):
            return False, 0, 9999

        # Security check: Test client bypass is ONLY permitted in active test environments (pytest/TESTING)
        is_test_env = bool(
            os.environ.get("PYTEST_CURRENT_TEST")
            or getattr(settings, "TESTING", False)
            or os.environ.get("ENV") == "test"
            or os.environ.get("TESTING") == "1"
        )
        if is_test_env and request.client and request.client.host == "testclient":
            return False, 0, 9999

        ip = self._get_client_ip(request)
        now = time.time()
        window_seconds = 60.0

        # Sensitive paths have stricter limits (OTP spam defense)
        if "/request-otp" in path or "/verify-otp" in path:
            max_allowed = settings.RATE_LIMIT_OTP_PER_MINUTE
        else:
            max_allowed = settings.RATE_LIMIT_PER_MINUTE

        with self._lock:
            # Periodic cleanup every 2 minutes
            if now - self._last_cleanup > 120.0:
                expired_before = now - 120.0
                dead_ips = []
                for k, stamps in self._requests.items():
                    valid = [t for t in stamps if t > expired_before]
                    if valid:
                        self._requests[k] = valid
                    else:
                        dead_ips.append(k)
                for k in dead_ips:
                    del self._requests[k]
                self._last_cleanup = now

            timestamps = self._requests.setdefault(ip, [])
            # Purge timestamps older than 60s
            cutoff = now - window_seconds
            valid_timestamps = [t for t in timestamps if t > cutoff]
            self._requests[ip] = valid_timestamps

            if len(valid_timestamps) >= max_allowed:
                return True, len(valid_timestamps), max_allowed

            valid_timestamps.append(now)
            return False, len(valid_timestamps), max_allowed


rate_limiter = RateLimiter()


class SecurityAndRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Rate Limiting Check
        is_limited, count, max_allowed = rate_limiter.is_rate_limited(request)
        if is_limited:
            logger.warning(f"Rate limit exceeded for {request.url.path} (count: {count}/{max_allowed})")
            return JSONResponse(
                status_code=429,
                content={
                    "ok": False,
                    "error": "RATE_LIMIT_EXCEEDED",
                    "detail": "So'rovlar soni me'yordan oshdi. Iltimos, 1 daqiqadan so'ng qayta urinib ko'ring.",
                },
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(max_allowed),
                    "X-RateLimit-Remaining": "0",
                },
            )

        # 2. Dispatch request
        try:
            response: Response = await call_next(request)
        except Exception:
            raise

        # 3. Security Headers (Helmet-equivalent protection)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Permissions-Policy"] = "geolocation=(self), microphone=(self), camera=(self)"

        return response
