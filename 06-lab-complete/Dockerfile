# syntax=docker/dockerfile:1
# ============================================================
# Production Dockerfile — multi-stage
#   Stage 1 (builder): cài dependencies vào virtualenv /opt/venv
#   Stage 2 (runtime): python slim + venv + code, user non-root, HEALTHCHECK
# ============================================================

# ---------- Stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
# Copy requirements trước để tận dụng layer cache: sửa code không phải cài lại deps
COPY requirements.txt .
RUN pip install -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2 \
    ENVIRONMENT=production

# User non-root
RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app

EXPOSE 8000

# Không có curl trong image slim → dùng Python gọi /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT', '8000'), timeout=3)"]

# `exec` thay shell bằng uvicorn → uvicorn là PID 1, nhận SIGTERM trực tiếp khi `docker stop`.
# PORT do platform (Railway/Render) cấp, mặc định 8000.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --no-access-log --timeout-graceful-shutdown 8"]
