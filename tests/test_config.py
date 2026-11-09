import os
import textwrap

import pytest

from core.config import Config
from core.exceptions import ConfigurationError


@pytest.fixture(autouse=True)
def clean_config_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("JOBAGENTS_") or key in {
            "AI_MODEL",
            "AI_PROVIDER",
            "AI_BASE_URL",
            "APP_ENVIRONMENT",
            "APPLICATION_ENVIRONMENT",
        }:
            monkeypatch.delenv(key, raising=False)


def test_yaml_and_env_precedence(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        textwrap.dedent(
            """
            agent:
              mode: observation
              scan_interval_minutes: 30
            ai:
              model: mixed
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBAGENTS_AGENT_SCAN_INTERVAL_MINUTES", "45")
    config = Config.load(config_file)
    assert config.get("agent.scan_interval_minutes") == 45
    assert config.get("agent.mode") == "observation"
    assert config.get("ai.model") == "mixed"
    assert config.get_int("agent.scan_interval_minutes") == 45


def test_invalid_mode_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("agent:\n  mode: yolo\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        Config.load(config_file)


def test_defaults_present(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("application:\n  environment: testing\n", encoding="utf-8")
    config = Config.load(config_file)
    assert config.get("execution.docker_enabled") is True
    assert config.get("security.require_automation_permission") is True
    assert config.get_bool("agent.enabled") is True
