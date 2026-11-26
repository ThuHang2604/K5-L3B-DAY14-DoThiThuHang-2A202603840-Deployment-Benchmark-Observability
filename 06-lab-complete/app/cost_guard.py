"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis — dùng chung cho mọi instance
  • Hai ngân sách: theo ngày (DAILY_BUDGET_USD) và theo tháng (MONTHLY_BUDGET_USD)
  • Đã tiêu ≥ ngân sách → HTTP 402 Payment Required, không gọi LLM nữa

Key Redis:
  cost:daily:YYYY-MM-DD   (hết hạn sau 2 ngày)
  cost:monthly:YYYY-MM    (hết hạn sau 32 ngày)
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

DAY_TTL = 2 * 24 * 3600
MONTH_TTL = 32 * 24 * 3600


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def estimate_tokens(text: str) -> int:
    """~4 ký tự / token (ước lượng thô, đủ cho mock LLM)."""
    return max(1, len(text) // 4)


def get_spending() -> dict:
    daily_key, monthly_key = _keys()
    daily, monthly = r.mget(daily_key, monthly_key)
    return {"daily_usd": float(daily or 0), "monthly_usd": float(monthly or 0)}


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → HTTP 402."""
    spent = get_spending()
    over = []
    if spent["daily_usd"] >= settings.daily_budget_usd:
        over.append(("daily", spent["daily_usd"], settings.daily_budget_usd))
    if spent["monthly_usd"] >= settings.monthly_budget_usd:
        over.append(("monthly", spent["monthly_usd"], settings.monthly_budget_usd))
    if over:
        period, used, budget = over[0]
        raise HTTPException(
            status_code=402,
            detail={
                "error": f"{period.capitalize()} LLM budget exceeded",
                "spent_usd": round(used, 6),
                "budget_usd": budget,
            },
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí request vào tổng ngày + tháng, trả về chi phí request."""
    cost = (input_tokens / 1000) * PRICE_INPUT_PER_1K + (output_tokens / 1000) * PRICE_OUTPUT_PER_1K
    daily_key, monthly_key = _keys()
    pipe = r.pipeline(transaction=True)
    pipe.incrbyfloat(daily_key, cost)
    pipe.expire(daily_key, DAY_TTL)
    pipe.incrbyfloat(monthly_key, cost)
    pipe.expire(monthly_key, MONTH_TTL)
    pipe.execute()
    return cost
