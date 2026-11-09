from app.adapters.base_adapter import ConnectionResult, TaskCandidate
from app.models import PlatformConfig, Task
from app.services.discovery_service import DiscoveryService
from app.services.settings_service import SettingsService


class FakeScanAdapter:
    def connect(self):
        return ConnectionResult(ok=True, status="ok", message="Connected as test-profile")

    def search_tasks(self, since=None):
        return [
            TaskCandidate(
                external_id="task-1",
                title="Example OpenTask task",
                url="https://opentask.ai/tasks/task-1",
                description="Example",
                reward=3.0,
                currency="USDC",
                raw={"task": {"id": "task-1", "title": "Example OpenTask task"}},
            )
        ]


def test_scan_platform_logs_connection_without_argument_conflict(app, monkeypatch):
    platform = PlatformConfig(
        name="OpenTask",
        slug="opentask",
        adapter="opentask",
        enabled=True,
        automation_permission="unknown",
        config={"limits": {"max_tasks_per_scan": 10}},
    )
    from core.database import db

    db.session.add(platform)
    db.session.commit()

    monkeypatch.setattr(
        "app.services.discovery_service.build_adapter", lambda *args, **kwargs: FakeScanAdapter()
    )
    settings = SettingsService(app.extensions["jobagents_config"])
    service = DiscoveryService(app.extensions["jobagents_config"], settings)

    result = service.scan_platform(platform)

    refreshed = db.session.get(PlatformConfig, platform.id)
    assert result["new_tasks"] == 1
    assert refreshed.last_scan_status == "ok"
    assert refreshed.status == "ok"
    task = Task.query.filter_by(platform_id=platform.id, external_id="task-1").first()
    assert task is not None
    assert task.currency == "USDC"
    assert float(task.reward_amount) == 3.0


def test_scan_platform_auth_error_is_reported(app, monkeypatch):
    platform = PlatformConfig(
        name="OpenTask",
        slug="opentask",
        adapter="opentask",
        enabled=True,
        automation_permission="unknown",
        config={},
    )
    from core.database import db

    db.session.add(platform)
    db.session.commit()

    class FailingAdapter:
        def connect(self):
            return ConnectionResult(ok=False, status="error", message="Invalid token")

    monkeypatch.setattr(
        "app.services.discovery_service.build_adapter", lambda *args, **kwargs: FailingAdapter()
    )
    settings = SettingsService(app.extensions["jobagents_config"])
    service = DiscoveryService(app.extensions["jobagents_config"], settings)

    import pytest
    from core.exceptions import PlatformError

    with pytest.raises(PlatformError):
        service.scan_platform(platform)
    refreshed = db.session.get(PlatformConfig, platform.id)
    assert refreshed.last_scan_status == "error"
    assert "Invalid token" in refreshed.last_scan_message
