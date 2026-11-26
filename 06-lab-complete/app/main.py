"""
Production AI Agent — Part 6 Final Project

  GET  /health   liveness  — luôn 200 khi process còn chạy (không phụ thuộc Redis)
  GET  /ready    readiness — 200 khi Redis kết nối được và app không đang tắt, ngược lại 503
  POST /ask      X-API-Key → rate limit (429) → cost guard (402) → mock LLM → ghi chi phí

Chạy:  uvicorn app.main:app --workers 2
Kiểm tra:  python check_production_ready.py
"""
import json
import logging
import signal
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import redis_client
from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "lvl": record.levelname,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(entry, ensure_ascii=False, default=str)


def _build_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    log.setLevel(settings.log_level)
    log.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.propagate = False  # không để root/uvicorn in lại dạng text
    return log


logger = _build_logger()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Graceful shutdown
#   uvicorn đã tự xử lý SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
#   chạy phần shutdown của lifespan rồi thoát. Ở đây chỉ "gắn thêm" một bước:
#   đánh dấu draining để /ready trả 503 (load balancer ngừng gửi traffic mới),
#   sau đó GỌI LẠI handler cũ của uvicorn — không thay thế nó.
# ─────────────────────────────────────────────────────────
_draining = threading.Event()
_in_flight = 0
_in_flight_lock = threading.Lock()


def _install_sigterm_hook() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    previous = signal.getsignal(signal.SIGTERM)
    if not callable(previous):
        # uvicorn bản cũ dùng loop.add_signal_handler → không có handler Python để gọi lại;
        # cài handler riêng lúc này sẽ làm mất cơ chế tắt của uvicorn, nên bỏ qua.
        return

    def _on_sigterm(signum, frame):
        _draining.set()
        log_event("sigterm_received", in_flight=_in_flight)
        previous(signum, frame)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:  # không ở main thread của interpreter
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate()
    if not settings.agent_api_key:
        log_event("config_warning", logging.WARNING,
                  detail="AGENT_API_KEY is empty — every /ask request will get 401")
    _install_sigterm_hook()
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              monthly_budget_usd=settings.monthly_budget_usd,
              redis_ok=redis_client.redis_healthy())
    yield
    # Tới đây uvicorn đã chờ xong mọi request in-flight
    _draining.set()
    redis_client.close()
    log_event("shutdown", detail="graceful shutdown complete", in_flight=_in_flight)


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan,
              docs_url=None if settings.is_production else "/docs",
              redoc_url=None, openapi_url=None if settings.is_production else "/openapi.json")
START_TIME = time.time()


# ─────────────────────────────────────────────────────────
# Middleware: request_id + log JSON cho mỗi request
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def access_log(request: Request, call_next):
    global _in_flight
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    start = time.perf_counter()
    with _in_flight_lock:
        _in_flight += 1
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        with _in_flight_lock:
            _in_flight -= 1
        log_event("request", request_id=request_id, method=request.method,
                  path=request.url.path, status=status,
                  ms=round((time.perf_counter() - start) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Error handling — mã lỗi rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]) or "body",
               "message": e.get("msg", "invalid value")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={
        "error": "Invalid request body. Expected JSON: {\"question\": \"...\"}",
        "details": errors,
        "request_id": getattr(request.state, "request_id", None),
    })


@app.exception_handler(redis.RedisError)
async def redis_error(request: Request, exc: redis.RedisError):
    log_event("redis_error", logging.ERROR, request_id=getattr(request.state, "request_id", None),
              error=type(exc).__name__)
    return JSONResponse(status_code=503, headers={"Retry-After": "5"}, content={
        "error": "Service temporarily unavailable (state store unreachable). Please retry.",
        "request_id": getattr(request.state, "request_id", None),
    })


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log_event("unhandled_error", logging.ERROR, request_id=getattr(request.state, "request_id", None),
              error=type(exc).__name__)
    return JSONResponse(status_code=500, content={
        "error": "Internal server error",
        "request_id": getattr(request.state, "request_id", None),
    })


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=64)


@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: process còn sống là 200 — KHÔNG kiểm tra Redis (Redis lỗi không phải lý do restart)."""
    return {"status": "ok", "environment": settings.environment,
            "uptime_seconds": round(time.time() - START_TIME, 1)}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được và app không đang tắt."""
    if _draining.is_set():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "shutting down"})
    if not redis_client.redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis unavailable"})
    return {"ready": True}


@app.post("/ask")
def ask(body: AskRequest, request: Request, api_key: str = Depends(verify_api_key)):
    limit_info = check_rate_limit(api_key)          # 429 khi vượt giới hạn
    check_budget()                                  # 402 khi hết ngân sách

    answer = llm_ask(body.question)

    input_tokens = estimate_tokens(body.question)
    output_tokens = estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log_event("llm_call", request_id=request.state.request_id,
              input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=round(cost, 8))

    return {
        "question": body.question,
        "answer": answer,
        "user_id": body.user_id,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
        "rate_limit": limit_info,
        "request_id": request.state.request_id,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, timeout_graceful_shutdown=8)
