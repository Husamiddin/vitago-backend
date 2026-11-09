import json
import logging
from typing import Any, Dict, Optional, Tuple
from pywebpush import WebPushException, webpush

from app.core.config import settings

logger = logging.getLogger(__name__)


def send_web_push(
    subscription_info: Dict[str, Any],
    payload: Dict[str, Any],
    ttl: int = 86400,
) -> Tuple[bool, str, Optional[str]]:
    """
    Delivers a Web Push notification to a browser push service endpoint.
    
    Args:
        subscription_info: Dict with keys {"endpoint": str, "keys": {"p256dh": str, "auth": str}}
        payload: Dict containing notification fields (title, message, icon, url, tag, etc.)
        ttl: Time-to-live in seconds (default 1 day)

    Returns:
        (success: bool, status: str, error_msg: Optional[str])
        status can be 'delivered', 'revoked' (for 404/410), or 'failed'
    """
    private_key = settings.VAPID_PRIVATE_KEY
    claims_sub = settings.VAPID_CLAIMS_SUB

    if not private_key or not claims_sub:
        logger.warning("[WebPush] VAPID private key or claims sub not configured.")
        return False, "skipped", "VAPID keys not configured"

    try:
        data_str = json.dumps(payload, ensure_ascii=False)
        webpush(
            subscription_info=subscription_info,
            data=data_str,
            vapid_private_key=private_key,
            vapid_claims={"sub": claims_sub},
            ttl=ttl,
        )
        return True, "delivered", None
    except WebPushException as exc:
        status_code = getattr(exc.response, "status_code", None) if exc.response is not None else None
        if status_code in (404, 410):
            logger.info("[WebPush] Subscription expired or revoked (status %s): %s", status_code, exc)
            return False, "revoked", f"Subscription expired or unregistered (HTTP {status_code})"
        
        err_msg = str(exc)
        logger.warning("[WebPush] WebPushException (status %s): %s", status_code, err_msg)
        return False, "failed", f"HTTP {status_code}: {err_msg[:120]}"
    except Exception as exc:
        err_msg = str(exc)
        logger.error("[WebPush] Unexpected error sending push: %s", err_msg)
        return False, "failed", err_msg[:120]
