import os
from datetime import timedelta

import click
from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from core.config import Config
from core.database import db
from core.logger import configure_logging, get_logger
from app.extensions import csrf, login_manager
from app.models import PlatformConfig, User
from app.services.settings_service import SettingsService


def create_app(config_path: str | None = None, overrides: dict | None = None) -> Flask:
    config = Config.load(config_path)
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
        SECRET_KEY=config.flask_secret_key,
        SQLALCHEMY_DATABASE_URI=(overrides or {}).get("SQLALCHEMY_DATABASE_URI") or config.database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_pre_ping": True,
            "pool_recycle": config.get_int("database.pool_recycle", 280),
            "pool_size": config.get_int("database.pool_size", 5),
        },
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.get("application.environment") == "production",
        PERMANENT_SESSION_LIFETIME=timedelta(
            minutes=config.get_int("application.session_lifetime_minutes", 480)
        ),
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        WTF_CSRF_TIME_LIMIT=None,
    )
    if overrides:
        app.config.update(overrides)

    configure_logging(config.get("logging.level", "INFO"))
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.extensions["jobagents_config"] = config

    from app.routes import auth, dashboard, earnings, platforms, settings as settings_routes, tasks

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(tasks.bp)
    app.register_blueprint(platforms.bp)
    app.register_blueprint(settings_routes.bp)
    app.register_blueprint(earnings.bp)

    register_cli(app)
    register_errors(app)
    register_context(app)

    @app.get("/healthz")
    def healthz():
        from core.timeutil import utcnow
        from app.models import WorkerState

        result = {"app": "ok", "time": utcnow().isoformat()}
        status_code = 200
        try:
            db.session.execute(db.text("SELECT 1"))
            result["database"] = "ok"
        except Exception:
            db.session.rollback()
            result["database"] = "error"
            status_code = 503
        try:
            worker = db.session.get(WorkerState, 1)
            result["worker"] = worker.status if worker else "unknown"
            result["worker_last_heartbeat"] = (
                worker.last_heartbeat.isoformat() if worker and worker.last_heartbeat else None
            )
        except Exception:
            db.session.rollback()
            result["worker"] = "unknown"
        return jsonify(result), status_code

    get_logger().info("Application initialized")
    return app


@login_manager.user_loader
def load_user(user_id: str):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def register_errors(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(error):
        return render_template("errors/400.html", error=error), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html", error=error), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html", error=error), 404

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        get_logger().exception("Unhandled error on %s", request.path)
        return render_template("errors/500.html", error=error), 500


def register_context(app: Flask) -> None:
    from app.services.budget_service import BudgetService

    @app.context_processor
    def inject_layout():
        try:
            settings = SettingsService(app.extensions["jobagents_config"])
            budget = BudgetService(settings)
            return {
                "app_name": settings.get("application.name", "AutonomousAgent"),
                "agent_mode": settings.get("agent.mode", "observation"),
                "budget_summary": budget.status(),
                "nav_pending_count": _pending_count(),
            }
        except Exception:
            return {
                "app_name": "AutonomousAgent",
                "agent_mode": "observation",
                "budget_summary": {},
                "nav_pending_count": 0,
            }

    @app.template_filter("money")
    def money_filter(value):
        try:
            return f"{float(value or 0):,.2f}"
        except (TypeError, ValueError):
            return "0.00"

    @app.template_filter("dt")
    def datetime_filter(value):
        if not value:
            return "—"
        return value.strftime("%Y-%m-%d %H:%M UTC")


def _pending_count() -> int:
    try:
        from app.models import Task

        return Task.query.filter(Task.status.in_(["pending", "manual_review"])).count()
    except Exception:
        return 0


def register_cli(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.option("--username", default=None)
    @click.option("--password", default=None)
    def create_admin(username, password):
        from core.security import hash_password
        from app.models import User

        username = username or click.prompt("Admin username")
        password = password or click.prompt("Admin password", hide_input=True, confirmation_prompt=True)
        if len(password) < 12:
            raise click.ClickException("Password must have at least 12 characters")
        existing = User.query.filter_by(username=username).first()
        if existing:
            existing.password_hash = hash_password(password)
            existing.is_admin = True
            existing.is_active_flag = True
            db.session.commit()
            click.echo(f"Updated admin user {username}")
            return
        db.session.add(User(username=username, password_hash=hash_password(password), is_admin=True))
        db.session.commit()
        click.echo(f"Created admin user {username}")

    @app.cli.command("init-db")
    def init_db():
        db.create_all()
        click.echo("Database tables created")

    @app.cli.command("seed")
    def seed():
        config = app.extensions["jobagents_config"]
        created = 0
        for item in config.get("bootstrap.platforms", []) or []:
            slug = item["slug"]
            existing = PlatformConfig.query.filter_by(slug=slug).first()
            if existing:
                continue
            db.session.add(
                PlatformConfig(
                    name=item["name"],
                    slug=slug,
                    adapter=item["adapter"],
                    enabled=bool(item.get("enabled", False)),
                    automation_permission=item.get("automation_permission", "unknown"),
                    scan_interval_minutes=int(item.get("scan_interval_minutes", 60)),
                    config=item.get("config", {}),
                )
            )
            created += 1
        db.session.commit()
        click.echo(f"Seeded {created} platform(s)")

    @app.cli.command("scan")
    @click.option("--platform-id", type=int, default=None)
    def scan(platform_id):
        from app.services.discovery_service import DiscoveryService
        from app.services.settings_service import SettingsService

        settings = SettingsService(app.extensions["jobagents_config"])
        summary = DiscoveryService(app.extensions["jobagents_config"], settings).scan_due_platforms(platform_id)
        click.echo(summary)

    @app.cli.command("evaluate")
    @click.option("--limit", type=int, default=20)
    def evaluate(limit):
        from app.services.budget_service import BudgetService
        from app.services.evaluator_service import EvaluatorService
        from app.services.settings_service import SettingsService

        config = app.extensions["jobagents_config"]
        settings = SettingsService(config)
        budget = BudgetService(settings)
        outcomes = EvaluatorService(config, settings, budget).evaluate_pending(limit)
        for outcome in outcomes:
            click.echo(f"task {outcome.task_id}: {outcome.status} ({outcome.reason})")
        click.echo(f"Evaluated {len(outcomes)} task(s)")

    @app.cli.command("execute")
    @click.option("--task-id", type=int, default=None)
    def execute(task_id):
        from app.models import Task, TaskStatus
        from app.services.budget_service import BudgetService
        from app.services.execution_service import ExecutionService
        from app.services.settings_service import SettingsService

        config = app.extensions["jobagents_config"]
        settings = SettingsService(config)
        budget = BudgetService(settings)
        service = ExecutionService(config, settings, budget)
        query = Task.query.filter(Task.status == TaskStatus.APPROVED)
        if task_id:
            query = Task.query.filter(Task.id == task_id)
        for task in query.limit(settings.get_int("agent.max_tasks_per_day", 10)).all():
            try:
                execution = service.execute(task, actor="cli")
                click.echo(f"task {task.id}: execution {execution.status}, task now {task.status}")
            except Exception as exc:
                click.echo(f"task {task.id}: failed: {exc}")

    @app.cli.command("worker-once")
    def worker_once():
        from worker.task_processor import TaskProcessor

        processor = TaskProcessor(app)
        result = processor.run_cycle()
        click.echo(result)

    @app.cli.command("doctor")
    def doctor():
        from execution.docker_executor import DockerExecutor

        config = app.extensions["jobagents_config"]
        settings = SettingsService(config)
        checks = []
        try:
            db.session.execute(db.text("SELECT 1"))
            checks.append(("database", True, "connected"))
        except Exception as exc:
            checks.append(("database", False, str(exc)[:200]))
        checks.append(("ai_api_key", bool(settings.get_ai_api_key()), "configured" if settings.get_ai_api_key() else "missing"))
        checks.append(("github_token", settings.has_platform_token(), "configured" if settings.has_platform_token() else "missing"))
        docker = DockerExecutor(settings.get("execution.docker_image", "jobagents-sandbox:latest"))
        docker_ok = docker.available()
        checks.append(("docker", docker_ok, "reachable" if docker_ok else "unavailable"))
        for name, ok, message in checks:
            click.echo(f"[{'OK' if ok else 'FAIL'}] {name}: {message}")
