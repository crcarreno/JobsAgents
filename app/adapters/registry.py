from core.config import Config
from core.exceptions import ConfigurationError
from app.adapters.base_adapter import BaseAdapter
from app.adapters.github_adapter import GitHubBountyAdapter
from app.services.settings_service import SettingsService

ADAPTERS = {
    "github_bounty": GitHubBountyAdapter,
}


def adapter_names() -> list[str]:
    return sorted(ADAPTERS.keys())


def build_adapter(platform, config: Config, settings: SettingsService) -> BaseAdapter:
    adapter_class = ADAPTERS.get(platform.adapter)
    if adapter_class is None:
        raise ConfigurationError(f"Unknown adapter: {platform.adapter}")
    token = ""
    if platform.adapter == "github_bounty":
        token = settings.get_platform_token()
    return adapter_class(platform, config, settings, token=token)
