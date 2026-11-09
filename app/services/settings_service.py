import json

from core.config import Config
from core.database import db
from core.exceptions import ConfigurationError
from core.security import decrypt_secret, encrypt_secret
from app.models import SystemSetting

BOOL = "bool"
INT = "int"
FLOAT = "float"
STRING = "string"

MUTABLE_KEYS = {
    "ai.provider": STRING,
    "ai.model": STRING,
    "ai.max_requests_per_task": INT,
    "ai.timeout_seconds": INT,
    "ai.max_output_tokens": INT,
    "agent.enabled": BOOL,
    "agent.mode": STRING,
    "agent.observation_mode": BOOL,
    "agent.scan_interval_minutes": INT,
    "agent.max_tasks_per_day": INT,
    "agent.max_concurrent_tasks": INT,
    "agent.max_task_age_days": INT,
    "agent.require_manual_claim_approval": BOOL,
    "agent.require_manual_delivery_approval": BOOL,
    "profitability.minimum_profit_usd": FLOAT,
    "profitability.minimum_hourly_rate_usd": FLOAT,
    "profitability.minimum_success_probability": FLOAT,
    "profitability.platform_fee_percent": FLOAT,
    "profitability.platform_fee_fixed_usd": FLOAT,
    "profitability.infrastructure_cost_per_task_usd": FLOAT,
    "profitability.assumed_ai_requests_per_task": INT,
    "budget.daily_limit_usd": FLOAT,
    "budget.monthly_limit_usd": FLOAT,
    "budget.max_ai_requests_per_day": INT,
    "execution.docker_enabled": BOOL,
    "execution.docker_image": STRING,
    "execution.network_enabled_by_default": BOOL,
    "execution.max_execution_minutes": INT,
    "execution.max_memory_mb": INT,
    "execution.max_cpus": FLOAT,
    "execution.workspace_root": STRING,
    "security.require_automation_permission": BOOL,
    "security.require_task_verification": BOOL,
    "security.require_manual_approval_for_unknown_platforms": BOOL,
    "security.enable_injection_scan": BOOL,
}

SECRET_KEYS = {
    "ai.api_key_enc": STRING,
    "platform.github_token_enc": STRING,
}

MODES = {"observation", "semiautomatic", "autonomous"}


def _parse(value, value_type):
    if value is None:
        return None
    if value_type == BOOL:
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if value_type == INT:
        return int(value)
    if value_type == FLOAT:
        return float(value)
    return value


def _serialize(value, value_type):
    if value_type == BOOL:
        return "true" if bool(value) else "false"
    return str(value)


class SettingsService:
    def __init__(self, config: Config):
        self.config = config

    def get(self, key: str, default=None):
        value_type = MUTABLE_KEYS.get(key)
        if value_type is None:
            return self.config.get(key, default)
        row = db.session.get(SystemSetting, key)
        if row is not None:
            return _parse(row.value, value_type)
        return self.config.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key, default)
        return int(value)

    def get_float(self, key: str, default: float = 0.0) -> float:
        value = self.get(key, default)
        return float(value)

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        return bool(value)

    def set(self, key: str, value, actor: str = "admin") -> None:
        if key not in MUTABLE_KEYS and key not in SECRET_KEYS:
            raise ConfigurationError(f"Setting is not editable: {key}")
        value_type = MUTABLE_KEYS.get(key) or SECRET_KEYS[key]
        self._validate(key, value)
        row = db.session.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key, value=_serialize(value, value_type), value_type=value_type)
            db.session.add(row)
        else:
            row.value = _serialize(value, value_type)
            row.value_type = value_type
        db.session.commit()

    def set_if_changed(self, key: str, value) -> bool:
        current = self.get(key)
        if current == value:
            return False
        self.set(key, value)
        return True

    def _validate(self, key: str, value) -> None:
        if key == "agent.mode" and value not in MODES:
            raise ConfigurationError("agent.mode must be observation, semiautomatic or autonomous")
        if key in {"budget.daily_limit_usd", "budget.monthly_limit_usd"} and float(value) < 0:
            raise ConfigurationError("Budget limits cannot be negative")
        if key == "agent.max_concurrent_tasks" and int(value) < 1:
            raise ConfigurationError("max_concurrent_tasks must be >= 1")
        if key == "agent.scan_interval_minutes" and int(value) < 1:
            raise ConfigurationError("scan_interval_minutes must be >= 1")
        if key == "profitability.minimum_success_probability" and not 0 <= float(value) <= 1:
            raise ConfigurationError("minimum_success_probability must be between 0 and 1")

    def delete(self, key: str) -> None:
        row = db.session.get(SystemSetting, key)
        if row is not None:
            db.session.delete(row)
            db.session.commit()

    def public_settings(self) -> dict:
        result = {}
        for key, value_type in MUTABLE_KEYS.items():
            result[key] = self.get(key)
        row = db.session.get(SystemSetting, "ai.api_key_enc")
        result["ai.api_key_configured"] = bool(self.config.ai_api_key or (row and row.value))
        return result

    def set_ai_api_key(self, api_key: str) -> None:
        if not api_key:
            raise ConfigurationError("API key cannot be empty")
        token = encrypt_secret(api_key, self.config.secret_key)
        self.set("ai.api_key_enc", token)

    def get_ai_api_key(self) -> str:
        env_value = self.config.ai_api_key
        if env_value:
            return env_value
        row = db.session.get(SystemSetting, "ai.api_key_enc")
        if row is not None and row.value:
            return decrypt_secret(row.value, self.config.secret_key)
        return ""

    def set_platform_token(self, token: str) -> None:
        if not token:
            self.delete("platform.github_token_enc")
            return
        self.set("platform.github_token_enc", encrypt_secret(token, self.config.secret_key))

    def get_platform_token(self) -> str:
        import os

        env_value = os.getenv("GITHUB_TOKEN", "")
        if env_value:
            return env_value
        row = db.session.get(SystemSetting, "platform.github_token_enc")
        if row is not None and row.value:
            return decrypt_secret(row.value, self.config.secret_key)
        return ""

    def has_platform_token(self) -> bool:
        import os

        if os.getenv("GITHUB_TOKEN"):
            return True
        row = db.session.get(SystemSetting, "platform.github_token_enc")
        return bool(row and row.value)

    def export_json(self) -> str:
        return json.dumps(self.public_settings(), indent=2, sort_keys=True)
