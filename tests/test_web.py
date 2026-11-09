def test_dashboard_requires_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_and_dashboard(logged_in_client):
    response = logged_in_client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_login_rejects_bad_password(client, admin):
    response = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert response.status_code == 200
    assert b"Invalid credentials" in response.data


def test_tasks_page_loads(logged_in_client):
    response = logged_in_client.get("/tasks/")
    assert response.status_code == 200


def test_settings_page_loads(logged_in_client):
    response = logged_in_client.get("/settings/")
    assert response.status_code == 200
    assert b"OpenCode Go" in response.data


def test_logout_clears_session(logged_in_client):
    logged_in_client.post("/logout")
    response = logged_in_client.get("/")
    assert response.status_code == 302


def test_task_detail_renders(app, logged_in_client):
    import hashlib

    from core.database import db
    from app.models import PlatformConfig, Task, TaskStatus

    platform = PlatformConfig(
        name="GitHub",
        slug="github_bounties",
        adapter="github_bounty",
        enabled=True,
        automation_permission="allowed",
        config={},
    )
    db.session.add(platform)
    db.session.commit()
    task = Task(
        platform_id=platform.id,
        external_id="acme/app#1",
        dedupe_hash=hashlib.sha256(b"acme/app#1").hexdigest(),
        title="Example task",
        description="Example description",
        url="https://github.com/acme/app/issues/1",
        reward_amount=10,
        status=TaskStatus.MANUAL_REVIEW,
        evaluation={"analysis": {"category": "code"}, "ai_used": False},
    )
    db.session.add(task)
    db.session.commit()
    response = logged_in_client.get(f"/tasks/{task.id}")
    assert response.status_code == 200
    assert b"Example task" in response.data


def test_platform_form_and_earnings_render(logged_in_client):
    assert logged_in_client.get("/platforms/new").status_code == 200
    assert logged_in_client.get("/earnings/").status_code == 200


def test_ai_connection_test_logs_without_argument_conflict(app, logged_in_client, monkeypatch):
    class FakeProvider:
        def test_connection(self):
            return True, "Connection OK"

    monkeypatch.setattr(
        "app.routes.settings.build_ai_provider", lambda *args, **kwargs: FakeProvider()
    )
    response = logged_in_client.post("/settings/test-ai", follow_redirects=True)
    assert response.status_code == 200
    assert b"AI connection: Connection OK" in response.data
    assert b"multiple values" not in response.data


def test_dashboard_with_platform(app, logged_in_client):
    from core.database import db
    from app.models import PlatformConfig

    db.session.add(
        PlatformConfig(
            name="GitHub Bounties",
            slug="github_bounties",
            adapter="github_bounty",
            enabled=True,
            automation_permission="unknown",
            config={"search_queries": ["x"]},
        )
    )
    db.session.commit()
    response = logged_in_client.get("/")
    assert response.status_code == 200
    assert b"GitHub Bounties" in response.data
