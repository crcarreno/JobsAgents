import logging
from types import SimpleNamespace

import httpx
import pytest
import respx

from core.exceptions import (
    AutomationNotAllowedError,
    PlatformAuthError,
    PlatformError,
    PlatformRateLimitError,
)
from app.adapters.base_adapter import TaskCandidate
from app.adapters.opentask_adapter import OpenTaskAdapter
from app.adapters.registry import ADAPTERS, adapter_names, build_adapter
from app.services.settings_service import SettingsService

API = "https://opentask.ai/api"
ME_URL = f"{API}/agent/me"
CAPS_URL = f"{API}/agent/me/capabilities"
TASKS_URL = f"{API}/tasks"
RECOMMENDATIONS_URL = f"{API}/agent/me/task-recommendations"
AGENT_DETAIL_URL = f"{API}/agent/tasks/task_1"
PUBLIC_DETAIL_URL = f"{API}/tasks/task_1"

ME_PAYLOAD = {
    "profile": {
        "id": "profile_1",
        "kind": "agent",
        "handle": "agent_demo",
        "displayName": "Agent Demo",
        "serviceListingStatus": "published",
    },
    "stats": {"tasksPosted": 2, "activeBids": 1, "contractsAsSeller": 0},
    "activePayoutMethodCount": 1,
}

LIST_TASK = {
    "id": "task_1",
    "title": "Build a Python parser",
    "skillsTags": ["python", "parsing"],
    "budgetText": "50 USDC",
    "budgetAmount": "50",
    "budgetCurrency": "USDC",
    "deadline": "2026-12-01T00:00:00.000Z",
    "executionMode": "bounty",
    "deliveryStructure": "single",
    "rewardTerms": None,
    "createdAt": "2026-11-01T10:00:00.000Z",
    "capabilityRequirements": [],
    "attachments": [],
    "owner": {"id": "owner_1", "handle": "owner", "displayName": "Owner", "kind": "human"},
}

DETAIL_TASK = {
    **LIST_TASK,
    "description": "Write a Python parser with tests and a short README.",
    "acceptanceCriteria": "Tests pass; README explains usage.",
    "expectedDeliverableTypes": ["pull request", "tests"],
    "status": "open",
    "visibility": "public",
    "moderationStatus": "approved",
    "rewardTerms": {
        "rewardAmountAtomic": "50000000",
        "rewardToken": "USDC",
        "rewardTokenDecimals": 6,
        "rewardNetwork": "base",
        "rewardPolicyVersion": "v1",
        "maxWinners": 1,
        "fundingStatus": "not_escrowed",
    },
    "reviewProfile": None,
    "counts": {"bids": 0, "entries": 0},
}

DETAIL_PAYLOAD = {
    "task": DETAIL_TASK,
    "entryQuota": {"remaining": 1},
    "availableActions": [{"action": "submit_entry"}],
}


def build_adapter_instance(app, platform_config=None, token="test-opentask-token"):
    settings = SettingsService(app.extensions["jobagents_config"])
    platform = SimpleNamespace(
        slug="opentask",
        adapter="opentask",
        automation_permission="unknown",
        config=platform_config
        or {
            "base_url": API,
            "public_base_url": "https://opentask.ai",
            "catalog_source": "public",
            "max_results": 30,
            "sort": "new",
        },
    )
    return OpenTaskAdapter(platform, app.extensions["jobagents_config"], settings, token=token)


def test_adapter_is_registered(app, monkeypatch):
    monkeypatch.setenv("OPENTASK_API_TOKEN", "env-token")
    assert "opentask" in adapter_names()
    assert ADAPTERS["opentask"] is OpenTaskAdapter
    platform = SimpleNamespace(
        slug="opentask",
        adapter="opentask",
        automation_permission="unknown",
        config={"base_url": API},
    )
    adapter = build_adapter(platform, app.extensions["jobagents_config"], SettingsService(app.extensions["jobagents_config"]))
    assert isinstance(adapter, OpenTaskAdapter)
    assert adapter.token == "env-token"


@respx.mock
def test_connect_with_valid_token(app):
    with app.app_context():
        adapter = build_adapter_instance(app)
        route = respx.get(ME_URL).mock(return_value=httpx.Response(200, json=ME_PAYLOAD))
        result = adapter.connect()
        assert route.called
        assert result.ok is True
        assert result.status == "ok"
        assert "agent_demo" in result.message
        assert result.details["profile_id"] == "profile_1"
        assert result.details["handle"] == "agent_demo"
        assert route.calls[0].request.headers["authorization"] == "Bearer test-opentask-token"


def test_connect_without_token_does_not_call_api(app):
    with app.app_context():
        adapter = build_adapter_instance(app, token="")
        result = adapter.connect()
        assert result.ok is False
        assert result.status == "not_configured"
        assert "OPENTASK_API_TOKEN" in result.message


@respx.mock
def test_connect_authentication_error(app):
    with app.app_context():
        adapter = build_adapter_instance(app)
        respx.get(ME_URL).mock(return_value=httpx.Response(401, json={"error": "Missing Authorization header"}))
        result = adapter.connect()
        assert result.ok is False
        assert result.status == "error"
        assert "invalid" in result.message.lower() or "missing" in result.message.lower()


@respx.mock
def test_public_catalog_normalization(app):
    with app.app_context():
        adapter = build_adapter_instance(app)
        route = respx.get(TASKS_URL).mock(
            return_value=httpx.Response(
                200, json={"tasks": [LIST_TASK], "nextCursor": None}
            )
        )
        candidates = adapter.search_tasks()
        assert route.called
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.external_id == "task_1"
        assert candidate.title == "Build a Python parser"
        assert candidate.url == "https://opentask.ai/tasks/task_1"
        assert candidate.reward == 50.0
        assert candidate.currency == "USDC"
        assert candidate.created_at is not None
        assert candidate.raw["task"]["id"] == "task_1"
        assert "query" not in route.calls[0].request.url.params
        assert route.calls[0].request.url.params["limit"] == "30"


@respx.mock
def test_empty_catalog(app):
    with app.app_context():
        adapter = build_adapter_instance(app)
        respx.get(TASKS_URL).mock(
            return_value=httpx.Response(200, json={"tasks": [], "nextCursor": None})
        )
        assert adapter.search_tasks() == []


@respx.mock
def test_recommendations_catalog_source(app):
    with app.app_context():
        adapter = build_adapter_instance(
            app,
            {
                "base_url": API,
                "catalog_source": "recommendations",
                "include_weak": True,
                "max_results": 5,
            },
        )
        respx.get(RECOMMENDATIONS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "recommendations": [
                        {"task": LIST_TASK, "score": 91, "scoreBucket": "excellent"}
                    ],
                    "meta": {"candidateCount": 1},
                },
            )
        )
        candidates = adapter.search_tasks()
        assert len(candidates) == 1
        assert candidates[0].external_id == "task_1"
        assert candidates[0].raw["recommendation"]["score"] == 91
        assert candidates[0].raw["source"] == "recommendations"


@respx.mock
def test_get_task_details_authenticated(app):
    with app.app_context():
        adapter = build_adapter_instance(app)
        route = respx.get(AGENT_DETAIL_URL).mock(
            return_value=httpx.Response(200, json=DETAIL_PAYLOAD)
        )
        candidate = TaskCandidate(
            external_id="task_1",
            title=LIST_TASK["title"],
            url="https://opentask.ai/tasks/task_1",
        )
        detail = adapter.get_task_details(candidate)
        assert route.called
        assert detail.external_id == "task_1"
        assert detail.description.startswith("Write a Python parser")
        assert detail.reward == 50.0
        assert detail.currency == "USDC"
        assert detail.expected_output == "pull request, tests"
        assert detail.extra["execution_mode"] == "bounty"
        assert detail.extra["funding_status"] == "not_escrowed"
        assert detail.extra["acceptance_criteria"].startswith("Tests pass")
        assert detail.deadline is not None
        assert detail.raw["entryQuota"]["remaining"] == 1


@respx.mock
def test_get_task_details_public_without_token(app):
    with app.app_context():
        adapter = build_adapter_instance(app, token="")
        route = respx.get(PUBLIC_DETAIL_URL).mock(
            return_value=httpx.Response(200, json={"task": DETAIL_TASK})
        )
        candidate = TaskCandidate(
            external_id="task_1", title=LIST_TASK["title"], url="https://opentask.ai/tasks/task_1"
        )
        detail = adapter.get_task_details(candidate)
        assert route.called
        assert detail.reward == 50.0
        assert "authorization" not in route.calls[0].request.headers


@respx.mock
def test_http_errors_and_timeouts(app):
    with app.app_context():
        adapter = build_adapter_instance(app)

        respx.get(ME_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "slow down"}),
                httpx.Response(500, json={"error": "server error"}),
                httpx.Response(
                    403,
                    json={"error": "insufficient scope", "code": "INSUFFICIENT_SCOPE"},
                ),
                httpx.ReadTimeout("timed out"),
            ]
        )

        with pytest.raises(PlatformRateLimitError) as rate_error:
            adapter._request("GET", "/agent/me")
        assert "7" in str(rate_error.value)

        with pytest.raises(PlatformError):
            adapter._request("GET", "/agent/me")

        with pytest.raises(PlatformAuthError) as forbidden:
            adapter._request("GET", "/agent/me")
        assert "INSUFFICIENT_SCOPE" in str(forbidden.value)

        with pytest.raises(PlatformError) as timeout_error:
            adapter._request("GET", "/agent/me")
        assert "timed out" in str(timeout_error.value).lower()


@respx.mock
def test_token_is_never_exposed(app, caplog):
    secret = "ot_super_secret_token_1234567890"
    with app.app_context():
        adapter = build_adapter_instance(app, token=secret)
        caplog.set_level(logging.DEBUG)
        respx.get(ME_URL).mock(
            return_value=httpx.Response(401, json={"error": "Missing Authorization header"})
        )
        result = adapter.connect()
        assert secret not in caplog.text
        assert secret not in result.message

        with pytest.raises(PlatformAuthError) as error:
            adapter._request("GET", "/agent/me")
        assert secret not in str(error.value)


@respx.mock
def test_write_operations_are_blocked(app):
    with app.app_context():
        adapter = build_adapter_instance(app)
        candidate = TaskCandidate(external_id="task_1", title="t", url="u")
        assert adapter.check_automation_permission(candidate)[0] == "unknown"
        with pytest.raises(AutomationNotAllowedError):
            adapter.claim_task(SimpleNamespace(external_id="task_1"))
        with pytest.raises(AutomationNotAllowedError):
            adapter.submit_task(SimpleNamespace(external_id="task_1"), [])
        payment = adapter.get_payment_status(SimpleNamespace(external_id="task_1"))
        assert payment["status"] == "unverifiable"
        assert "not_escrowed" in payment["message"]
