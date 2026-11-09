from datetime import datetime

from sqlalchemy import case, func

from core.database import db
from core.exceptions import AIBudgetExceeded, AILimitExceeded
from core.timeutil import utcnow
from app.models import AIUsage
from app.services.settings_service import SettingsService


class BudgetService:
    def __init__(self, settings: SettingsService):
        self.settings = settings

    def _sum_cost(self, start: datetime) -> float:
        billed_amount = case(
            (AIUsage.cost > 0, AIUsage.cost),
            else_=func.coalesce(AIUsage.allowance, 0),
        )
        value = (
            db.session.query(func.coalesce(func.sum(billed_amount), 0))
            .filter(AIUsage.created_at >= start)
            .scalar()
        )
        return float(value or 0)

    def _count_requests(self, start: datetime) -> int:
        value = (
            db.session.query(func.count(AIUsage.id)).filter(AIUsage.created_at >= start).scalar()
        )
        return int(value or 0)

    def spent_today(self) -> float:
        now = utcnow()
        return self._sum_cost(now.replace(hour=0, minute=0, second=0, microsecond=0))

    def spent_this_month(self) -> float:
        now = utcnow()
        return self._sum_cost(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))

    def requests_today(self) -> int:
        now = utcnow()
        return self._count_requests(now.replace(hour=0, minute=0, second=0, microsecond=0))

    def status(self) -> dict:
        daily_limit = self.settings.get_float("budget.daily_limit_usd", 0.5)
        monthly_limit = self.settings.get_float("budget.monthly_limit_usd", 10.0)
        max_requests = self.settings.get_int("budget.max_ai_requests_per_day", 200)
        spent_today = self.spent_today()
        spent_month = self.spent_this_month()
        return {
            "spent_today": spent_today,
            "spent_month": spent_month,
            "daily_limit": daily_limit,
            "monthly_limit": monthly_limit,
            "requests_today": self.requests_today(),
            "max_requests_per_day": max_requests,
            "daily_remaining": max(0.0, daily_limit - spent_today),
            "monthly_remaining": max(0.0, monthly_limit - spent_month),
            "allowed": spent_today < daily_limit
            and spent_month < monthly_limit
            and self.requests_today() < max_requests,
        }

    def ensure_can_spend(self, estimated_cost: float = 0.0) -> None:
        daily_limit = self.settings.get_float("budget.daily_limit_usd", 0.5)
        monthly_limit = self.settings.get_float("budget.monthly_limit_usd", 10.0)
        max_requests = self.settings.get_int("budget.max_ai_requests_per_day", 200)
        spent_today = self.spent_today()
        spent_month = self.spent_this_month()
        if spent_today + estimated_cost > daily_limit:
            raise AIBudgetExceeded(
                "Daily AI budget exhausted", spent_today=spent_today, limit=daily_limit
            )
        if spent_month + estimated_cost > monthly_limit:
            raise AIBudgetExceeded(
                "Monthly AI budget exhausted", spent_month=spent_month, limit=monthly_limit
            )
        if self.requests_today() >= max_requests:
            raise AILimitExceeded("Daily AI request limit reached", limit=max_requests)
