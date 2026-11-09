import hashlib

import pytest

from core.database import db
from core.exceptions import TaskStateError
from core.timeutil import utcnow
from app.adapters.base_adapter import TaskCandidate, TaskDetail
from app.models import PlatformConfig, Task, TaskStatus
from app.services.budget_service import BudgetService
from app.services.delivery_service import DeliveryService
from app.services.evaluator_service import EvaluatorService
from app.services.execution_service import ExecutionService
from app.services.settings_service import SettingsService
from app.services.task_service import transition


class FakeAdapter:
    def __init__(self, permission="allowed"):
        self.permission = permission

    def get_task_details(self, candidate):
        return TaskDetail(
            external_id=candidate.external_id,
            title=candidate.title,
            url=candidate.url,
            description=candidate.description,
            reward=candidate.reward,
            currency="USD",
            expected_output="A pull request",
            language="Python",
            extra={"repo": "acme/app", "number": 7, "default_branch": "main"},
            raw=candidate.raw,
        )

    def check_automation_permission(self, candidate):
        return self.permission, "permission checked by fake adapter"


def make_platform(app, permission="allowed"):
    platform = PlatformConfig(
        name="GitHub Bounties",
        slug="github_bounties",
        adapter="github_bounty",
        enabled=True,
        automation_permission=permission,
        config={"search_queries": ["x"]},
    )
    db.session.add(platform)
    db.session.commit()
    return platform


def make_task(platform, external_id="acme/app#7", reward=50.0):
    task = Task(
        platform_id=platform.id,
        external_id=external_id,
        dedupe_hash=hashlib.sha256(external_id.encode()).hexdigest(),
        title="Fix parser",
        description="Bounty $50 for fixing the parser with tests.",
        url="https://github.com/acme/app/issues/7",
        reward_amount=reward,
        currency="USD",
        status=TaskStatus.PENDING,
        raw_payload={"repo": "acme/app", "number": 7},
    )
    db.session.add(task)
    db.session.commit()
    return task


def test_invalid_transition_is_rejected(app):
    platform = make_platform(app)
    task = make_task(platform)
    with pytest.raises(TaskStateError):
        transition(task, TaskStatus.SUBMITTED)


def test_transition_records_history(app):
    platform = make_platform(app)
    task = make_task(platform)
    transition(task, TaskStatus.EVALUATING, "start", actor="test")
    assert task.status == TaskStatus.EVALUATING
    assert task.status_history.count() == 1


def test_evaluation_without_ai_goes_to_manual_review(app, monkeypatch):
    platform = make_platform(app)
    task = make_task(platform)
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setattr("app.services.evaluator_service.build_adapter", lambda *args: FakeAdapter())
    settings = SettingsService(app.extensions["jobagents_config"])
    budget = BudgetService(settings)
    outcome = EvaluatorService(app.extensions["jobagents_config"], settings, budget).evaluate_task(task)
    refreshed = db.session.get(Task, task.id)
    assert outcome.status == TaskStatus.MANUAL_REVIEW
    assert refreshed.automation_allowed is True
    assert float(refreshed.net_profit) > 0
    assert refreshed.evaluation["ai_used"] is False


def test_evaluation_rejects_unprofitable_task(app, monkeypatch):
    platform = make_platform(app)
    task = make_task(platform, reward=0.01)
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setattr("app.services.evaluator_service.build_adapter", lambda *args: FakeAdapter())
    settings = SettingsService(app.extensions["jobagents_config"])
    budget = BudgetService(settings)
    outcome = EvaluatorService(app.extensions["jobagents_config"], settings, budget).evaluate_task(task)
    assert outcome.status == TaskStatus.REJECTED


def test_execution_blocked_in_observation_mode(app):
    platform = make_platform(app)
    task = make_task(platform)
    task.status = TaskStatus.APPROVED
    task.automation_allowed = True
    db.session.commit()
    settings = SettingsService(app.extensions["jobagents_config"])
    service = ExecutionService(app.extensions["jobagents_config"], settings, BudgetService(settings))
    with pytest.raises(TaskStateError):
        service.execute(task, actor="test", manual=True)


def test_delivery_is_idempotent(app):
    platform = make_platform(app)
    task = make_task(platform)
    task.status = TaskStatus.SUBMITTED
    task.submitted_at = utcnow()
    task.delivery_reference = "123"
    db.session.commit()
    settings = SettingsService(app.extensions["jobagents_config"])
    result = DeliveryService(app.extensions["jobagents_config"], settings).deliver(task, actor="test", manual=True)
    assert result["status"] == "already_submitted"


def test_worker_cycle_respects_disabled_agent(app):
    settings = SettingsService(app.extensions["jobagents_config"])
    settings.set("agent.enabled", False)
    from worker.task_processor import TaskProcessor

    processor = TaskProcessor(app)
    summary = processor.run_cycle()
    assert summary.get("skipped") == "agent disabled"
