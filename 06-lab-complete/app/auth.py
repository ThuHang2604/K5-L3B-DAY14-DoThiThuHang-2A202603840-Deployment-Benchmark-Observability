"""
API Key authentication (tiêu chí 6).

  • Client gửi header  X-API-Key: <key>
  • Key phải trùng biến môi trường AGENT_API_KEY
  • Thiếu hoặc sai key → HTTP 401 (không phải 403 hay 422)
"""
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

# auto_error=False: tự trả 401 thay vì 403 mặc định của FastAPI
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Invalid or missing API key. Send header: X-API-Key: <key>",
    headers={"WWW-Authenticate": "ApiKey"},
)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho /ask. Trả về key hợp lệ (dùng làm định danh cho rate limit)."""
    expected = settings.agent_api_key
    if not api_key or not expected:
        raise _UNAUTHORIZED
    # so sánh constant-time để tránh timing attack
    if not hmac.compare_digest(api_key.encode(), expected.encode()):
        raise _UNAUTHORIZED
    return api_key
