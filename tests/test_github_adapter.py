from types import SimpleNamespace

import httpx
import respx

from app.adapters.github_adapter import GitHubBountyAdapter
from app.services.settings_service import SettingsService


def build_adapter(app, platform_config=None):
    settings = SettingsService(app.extensions["jobagents_config"])
    platform = SimpleNamespace(
        slug="github_bounties",
        automation_permission="unknown",
        config=platform_config
        or {
            "search_queries": ["is:issue is:open label:bounty"],
            "allowed_repos": [],
            "max_results": 30,
            "min_reward_usd": 0.5,
        },
    )
    return GitHubBountyAdapter(platform, app.extensions["jobagents_config"], settings, token="")


def sample_issue(**overrides):
    issue = {
        "number": 7,
        "title": "Fix the parser",
        "html_url": "https://github.com/acme/app/issues/7",
        "repository_url": "https://api.github.com/repos/acme/app",
        "body": "Bounty: $50 for a fix with tests.",
        "labels": [{"name": "bounty"}],
        "state": "open",
        "created_at": "2026-10-01T10:00:00Z",
        "updated_at": "2026-10-02T10:00:00Z",
        "comments": 3,
        "user": {"login": "maintainer"},
        "assignees": [],
    }
    issue.update(overrides)
    return issue


def test_parse_issue_extracts_reward(app):
    with app.app_context():
        adapter = build_adapter(app)
        candidate = adapter._parse_issue(sample_issue())
        assert candidate is not None
        assert candidate.external_id == "acme/app#7"
        assert candidate.reward == 50.0
        assert candidate.currency == "USD"


def test_parse_issue_skips_pull_requests(app):
    with app.app_context():
        adapter = build_adapter(app)
        assert adapter._parse_issue(sample_issue(pull_request={"url": "x"})) is None


def test_parse_issue_skips_without_reward(app):
    with app.app_context():
        adapter = build_adapter(app)
        issue = sample_issue(body="Please fix this", labels=[{"name": "bug"}], title="Fix it")
        assert adapter._parse_issue(issue) is None


def test_parse_issue_respects_allowed_repos(app):
    with app.app_context():
        adapter = build_adapter(app, {"search_queries": ["x"], "allowed_repos": ["other/repo"]})
        assert adapter._parse_issue(sample_issue()) is None


def test_automation_denied_by_issue_text(app):
    with app.app_context():
        adapter = build_adapter(app)
        candidate = adapter._parse_issue(
            sample_issue(body="Bounty $50. No AI or bots allowed on this repository.")
        )
        status, notes = adapter.check_automation_permission(candidate)
        assert status == "denied"
        assert "denies" in notes


def test_automation_unknown_by_default(app):
    with app.app_context():
        adapter = build_adapter(app)
        candidate = adapter._parse_issue(sample_issue())
        status, _notes = adapter.check_automation_permission(candidate)
        assert status == "unknown"


def test_automation_allowed_by_operator_config(app):
    with app.app_context():
        adapter = build_adapter(
            app,
            {"search_queries": ["x"], "automation_allowed_repos": ["acme/app"]},
        )
        candidate = adapter._parse_issue(sample_issue())
        status, _notes = adapter.check_automation_permission(candidate)
        assert status == "allowed"


@respx.mock
def test_search_tasks_uses_official_api(app):
    with app.app_context():
        adapter = build_adapter(app, {"search_queries": ["is:issue label:bounty"], "max_results": 1})
        route = respx.get("https://api.github.com/search/issues").mock(
            return_value=httpx.Response(200, json={"items": [sample_issue()]})
        )
        results = adapter.search_tasks()
        assert route.called
        assert len(results) == 1
        assert results[0].external_id == "acme/app#7"


@respx.mock
def test_connect_without_token_is_read_only(app):
    with app.app_context():
        adapter = build_adapter(app)
        connection = adapter.connect()
        assert connection.ok is False
        assert connection.status == "not_configured"


@respx.mock
def test_get_task_details_includes_repo_metadata(app):
    with app.app_context():
        adapter = build_adapter(app)
        candidate = adapter._parse_issue(sample_issue())
        respx.get("https://api.github.com/repos/acme/app/issues/7").mock(
            return_value=httpx.Response(200, json=sample_issue())
        )
        respx.get("https://api.github.com/repos/acme/app/issues/7/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get("https://api.github.com/repos/acme/app").mock(
            return_value=httpx.Response(200, json={"default_branch": "main", "language": "Python"})
        )
        detail = adapter.get_task_details(candidate)
        assert detail.extra["default_branch"] == "main"
        assert detail.language == "Python"
        assert detail.reward == 50.0
