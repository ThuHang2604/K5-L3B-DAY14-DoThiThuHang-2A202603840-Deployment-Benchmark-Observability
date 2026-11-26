"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

  • Mỗi biến có giá trị mặc định hợp lý để chạy local.
  • Secret (AGENT_API_KEY) KHÔNG có giá trị mặc định trong code:
      - development / staging: thiếu key → mọi request /ask bị 401 (log cảnh báo)
      - production: thiếu key → dừng ngay khi khởi động (fail fast)
  • Danh sách biến đầy đủ: xem .env.example
"""
import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: _env_str("ENVIRONMENT", "development").lower())
    log_level: str = field(default_factory=lambda: _env_str("LOG_LEVEL", "INFO").upper())

    # Secret — chỉ đọc từ env, không có default
    agent_api_key: str = field(default_factory=lambda: _env_str("AGENT_API_KEY"))

    # Redis: state dùng chung cho rate limiter + cost guard (stateless app)
    redis_url: str = field(default_factory=lambda: _env_str("REDIS_URL", "redis://localhost:6379/0"))

    # Rate limit: số request tối đa mỗi API key trong 60 giây
    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    rate_limit_window_seconds: int = 60

    # Cost guard: ngân sách LLM (USD)
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))
    monthly_budget_usd: float = field(default_factory=lambda: _env_float("MONTHLY_BUDGET_USD", 10.0))

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        """Gọi lúc khởi động. Production thiếu secret → dừng luôn thay vì chạy sai."""
        if self.is_production and not self.agent_api_key:
            raise RuntimeError("AGENT_API_KEY is required when ENVIRONMENT=production")


settings = Settings()
