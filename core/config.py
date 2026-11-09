import copy
import os
from pathlib import Path
from typing import Any

import yaml

from core.exceptions import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

DEFAULTS: dict[str, Any] = {
    "application": {
        "name": "AutonomousAgent",
        "environment": "production",
        "timezone": "America/Guayaquil",
        "secret_key_env": "APP_SECRET_KEY",
        "base_url": "",
        "session_lifetime_minutes": 480,
    },
    "database": {
        "url_env": "DATABASE_URL",
        "pool_size": 5,
        "pool_recycle": 280,
    },
    "ai": {
        "provider": "opencode_go",
        "model": "mixed",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "api_key_env": "AI_API_KEY",
        "max_requests_per_task": 10,
        "timeout_seconds": 300,
        "max_output_tokens": 4000,
        "temperature": 0.2,
        "retries": 2,
        "session_header": "x-opencode-session",
        "user_agent": "jobagents-agent/1.0",
        "price_per_1k_input_usd": 0.0003,
        "price_per_1k_output_usd": 0.0012,
        "pricing": {
            "deepseek-v4.1-flash": {"input_per_1m": 0.30, "output_per_1m": 1.20},
            "kimi-k2.7-code": {"input_per_1m": 0.95, "output_per_1m": 4.00},
        },
        "profiles": {
            "mixed": {
                "analysis": "deepseek-v4.1-flash",
                "execution": "kimi-k2.7-code",
                "verification": "deepseek-v4.1-flash",
            }
        },
    },
    "agent": {
        "enabled": True,
        "mode": "observation",
        "observation_mode": True,
        "scan_interval_minutes": 30,
        "max_tasks_per_day": 10,
        "max_concurrent_tasks": 1,
        "max_task_age_days": 30,
        "require_manual_claim_approval": True,
        "require_manual_delivery_approval": True,
    },
    "profitability": {
        "minimum_profit_usd": 0.10,
        "minimum_hourly_rate_usd": 0.50,
        "minimum_success_probability": 0.35,
        "platform_fee_percent": 0.0,
        "platform_fee_fixed_usd": 0.0,
        "infrastructure_cost_per_task_usd": 0.01,
        "assumed_ai_requests_per_task": 6,
    },
    "budget": {
        "daily_limit_usd": 0.50,
        "monthly_limit_usd": 10.00,
        "max_ai_requests_per_day": 200,
    },
    "execution": {
        "docker_enabled": True,
        "docker_image": "jobagents-sandbox:latest",
        "network_enabled_by_default": False,
        "max_execution_minutes": 15,
        "max_memory_mb": 512,
        "max_cpus": 0.5,
        "max_pids": 128,
        "workspace_root": "var/workspaces",
        "artifacts_dir": "var/artifacts",
        "allow_host_execution": False,
    },
    "security": {
        "require_automation_permission": True,
        "require_task_verification": True,
        "require_manual_approval_for_unknown_platforms": True,
        "max_task_description_chars": 20000,
        "enable_injection_scan": True,
        "login_max_attempts": 5,
        "login_lockout_minutes": 15,
    },
    "worker": {
        "heartbeat_seconds": 30,
        "lock_file": "var/worker.lock",
        "stale_after_seconds": 120,
        "poll_seconds": 5,
    },
    "logging": {"level": "INFO"},
}

ENV_PREFIX = "JOBAGENTS_"


def normalize_database_url(url: str) -> str:
    if url and url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+pymysql://", 1)
    return url


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _coerce(value: str, reference: Any) -> Any:
    if isinstance(reference, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(data: dict) -> dict:
    result = copy.deepcopy(data)
    known_paths = [("ai.max_requests_per_task", "AI_MAX_REQUESTS_PER_TASK")]

    def walk(node, prefix):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(value, path)
            else:
                known_paths.append((path, ENV_PREFIX + path.upper().replace(".", "_")))

    walk(result, "")

    for path, env_name in known_paths:
        raw = os.getenv(env_name)
        if raw is None:
            continue
        parts = path.split(".")
        target = result
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = _coerce(raw, target.get(parts[-1]))

    direct = {
        "APP_ENVIRONMENT": "application.environment",
        "APPLICATION_ENVIRONMENT": "application.environment",
        "APP_TIMEZONE": "application.timezone",
        "AI_PROVIDER": "ai.provider",
        "AI_MODEL": "ai.model",
        "AI_BASE_URL": "ai.base_url",
        "LOG_LEVEL": "logging.level",
    }
    for env_name, path in direct.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        parts = path.split(".")
        target = result
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = raw

    return result


class Config:
    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self._data = data
        self.path = path
        self.validate()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        config_path = Path(path or os.getenv("JOBAGENTS_CONFIG") or DEFAULT_CONFIG_PATH)
        file_data: dict[str, Any] = {}
        if config_path.exists():
            try:
                with config_path.open("r", encoding="utf-8") as handle:
                    file_data = yaml.safe_load(handle) or {}
            except yaml.YAMLError as exc:
                raise ConfigurationError(f"Invalid YAML configuration: {exc}") from exc
        else:
            if os.getenv("JOBAGENTS_CONFIG"):
                raise ConfigurationError(f"Configuration file not found: {config_path}")
        merged = _deep_merge(DEFAULTS, file_data)
        merged = _apply_env_overrides(merged)
        return cls(merged, config_path)

    def validate(self) -> None:
        mode = self.get("agent.mode")
        if mode not in {"observation", "semiautomatic", "autonomous"}:
            raise ConfigurationError(f"Invalid agent.mode: {mode}")
        if float(self.get("profitability.minimum_profit_usd")) < 0:
            raise ConfigurationError("profitability.minimum_profit_usd must be >= 0")
        if int(self.get("agent.max_concurrent_tasks")) < 1:
            raise ConfigurationError("agent.max_concurrent_tasks must be >= 1")
        docker = self.get("execution.docker_enabled")
        allow_host = self.get("execution.allow_host_execution")
        if not docker and allow_host:
            if self.get("application.environment") == "production":
                raise ConfigurationError("Host execution is not allowed in production")

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, path: str) -> Any:
        value = self.get(path)
        if value is None:
            raise ConfigurationError(f"Missing required configuration: {path}")
        return value

    def get_int(self, path: str, default: int = 0) -> int:
        value = self.get(path, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def get_float(self, path: str, default: float = 0.0) -> float:
        value = self.get(path, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def get_bool(self, path: str, default: bool = False) -> bool:
        value = self.get(path, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)

    @property
    def secret_key(self) -> str:
        env_name = self.get("application.secret_key_env", "APP_SECRET_KEY")
        value = os.getenv(env_name, "")
        if not value:
            if self.get("application.environment") == "production":
                raise ConfigurationError(f"{env_name} must be set in production")
            value = "dev-insecure-secret-key"
        return value

    @property
    def flask_secret_key(self) -> str:
        return os.getenv("FLASK_SECRET_KEY") or self.secret_key

    @property
    def database_url(self) -> str:
        env_name = self.get("database.url_env", "DATABASE_URL")
        url = os.getenv(env_name)
        if not url:
            raise ConfigurationError(f"{env_name} is not set")
        return normalize_database_url(url)

    @property
    def ai_api_key(self) -> str:
        env_name = self.get("ai.api_key_env", "AI_API_KEY")
        return os.getenv(env_name, "")

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path
