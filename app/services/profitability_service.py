from dataclasses import dataclass

from app.services.settings_service import SettingsService


@dataclass
class ProfitabilityResult:
    reward: float
    platform_fee: float
    ai_cost: float
    infrastructure_cost: float
    net_profit: float
    hourly_rate: float
    estimated_minutes: int
    viable: bool
    reasons: list[str]


class ProfitabilityService:
    def __init__(self, settings: SettingsService):
        self.settings = settings

    def estimate_ai_cost(self, requests: int | None = None) -> float:
        assumed = requests or self.settings.get_int("profitability.assumed_ai_requests_per_task", 6)
        per_1k_in = self.settings.get_float("ai.price_per_1k_input_usd", 0.00015)
        per_1k_out = self.settings.get_float("ai.price_per_1k_output_usd", 0.0006)
        return assumed * (1.0 * per_1k_in + 0.3 * per_1k_out)

    def calculate(
        self,
        reward: float,
        estimated_minutes: int,
        success_probability: float,
        ai_cost: float | None = None,
        platform_fee_percent: float | None = None,
        platform_fee_fixed: float | None = None,
    ) -> ProfitabilityResult:
        reward = max(0.0, float(reward or 0))
        minutes = max(1, int(estimated_minutes or 1))
        probability = min(1.0, max(0.0, float(success_probability or 0)))
        fee_percent = (
            self.settings.get_float("profitability.platform_fee_percent", 0.0)
            if platform_fee_percent is None
            else platform_fee_percent
        )
        fee_fixed = (
            self.settings.get_float("profitability.platform_fee_fixed_usd", 0.0)
            if platform_fee_fixed is None
            else platform_fee_fixed
        )
        infra = self.settings.get_float("profitability.infrastructure_cost_per_task_usd", 0.01)
        ai_cost = self.estimate_ai_cost() if ai_cost is None else float(ai_cost)

        platform_fee = reward * fee_percent + fee_fixed
        expected_reward = reward * probability
        net_profit = expected_reward - platform_fee - ai_cost - infra
        hourly_rate = net_profit / (minutes / 60.0) if minutes else 0.0

        reasons = []
        min_profit = self.settings.get_float("profitability.minimum_profit_usd", 0.10)
        min_hourly = self.settings.get_float("profitability.minimum_hourly_rate_usd", 0.50)
        min_probability = self.settings.get_float("profitability.minimum_success_probability", 0.35)

        if net_profit < min_profit:
            reasons.append(f"net profit {net_profit:.4f} below minimum {min_profit:.2f}")
        if hourly_rate < min_hourly:
            reasons.append(f"hourly rate {hourly_rate:.2f} below minimum {min_hourly:.2f}")
        if probability < min_probability:
            reasons.append(f"success probability {probability:.2f} below minimum {min_probability:.2f}")
        if reward <= 0:
            reasons.append("task has no verifiable reward")

        return ProfitabilityResult(
            reward=reward,
            platform_fee=round(platform_fee, 4),
            ai_cost=round(ai_cost, 6),
            infrastructure_cost=round(infra, 6),
            net_profit=round(net_profit, 4),
            hourly_rate=round(hourly_rate, 4),
            estimated_minutes=minutes,
            viable=not reasons,
            reasons=reasons,
        )
