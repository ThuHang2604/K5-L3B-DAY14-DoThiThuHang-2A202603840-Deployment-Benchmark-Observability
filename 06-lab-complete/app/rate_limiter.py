"""
Rate limiting (tiêu chí 7) — sliding window trên Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis → mọi worker / instance dùng chung, còn nguyên sau khi restart agent

Thuật toán (1 round-trip, trong MULTI/EXEC nên atomic):
  ZREMRANGEBYSCORE  xoá các request cũ hơn 60s
  ZADD              ghi request hiện tại
  ZCARD             đếm số request trong cửa sổ
  EXPIRE            key tự hết hạn khi client ngừng gọi
Nếu đếm > limit thì xoá lại request vừa ghi (request bị chặn không chiếm chỗ) và trả 429.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r


def _bucket_key(key: str) -> str:
    # Không lưu API key thô vào Redis — chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> dict:
    limit = settings.rate_limit_per_minute
    window = settings.rate_limit_window_seconds
    bucket = _bucket_key(key)
    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"

    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(bucket, 0, now - window)
    pipe.zadd(bucket, {member: now})
    pipe.zcard(bucket)
    pipe.expire(bucket, window + 1)
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(bucket, member)
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        retry_after = window
        if oldest:
            retry_after = max(1, math.ceil(oldest[0][1] + window - now))
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": limit,
                "window_seconds": window,
                "retry_after_seconds": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    return {"limit": limit, "remaining": max(0, limit - count)}
