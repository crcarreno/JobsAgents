from app.services.profitability_service import ProfitabilityService
from app.services.settings_service import SettingsService


def test_profitable_task(app):
    settings = SettingsService(app.extensions["jobagents_config"])
    service = ProfitabilityService(settings)
    result = service.calculate(reward=20.0, estimated_minutes=30, success_probability=0.8)
    assert result.net_profit > 0
    assert result.hourly_rate > 30
    assert result.viable is True
    assert result.platform_fee == 0


def test_unprofitable_task(app):
    settings = SettingsService(app.extensions["jobagents_config"])
    service = ProfitabilityService(settings)
    result = service.calculate(reward=0.05, estimated_minutes=120, success_probability=0.5)
    assert result.viable is False
    assert any("net profit" in reason for reason in result.reasons)


def test_probability_gate(app):
    settings = SettingsService(app.extensions["jobagents_config"])
    settings.set("profitability.minimum_success_probability", 0.9)
    service = ProfitabilityService(settings)
    result = service.calculate(reward=50.0, estimated_minutes=30, success_probability=0.4)
    assert result.viable is False
    assert any("success probability" in reason for reason in result.reasons)


def test_platform_fee_is_subtracted(app):
    settings = SettingsService(app.extensions["jobagents_config"])
    service = ProfitabilityService(settings)
    result = service.calculate(
        reward=10.0, estimated_minutes=30, success_probability=1.0, platform_fee_percent=0.1
    )
    assert result.platform_fee == 1.0
    assert result.net_profit < 10.0
