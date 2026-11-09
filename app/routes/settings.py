from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from core.exceptions import ConfigurationError
from app.models import PlatformConfig
from app.routes.helpers import admin_required, get_budget, get_config, get_settings
from app.services.log_service import LogService
from app.providers import build_ai_provider

bp = Blueprint("settings", __name__, url_prefix="/settings")

GENERAL_FIELDS = {
    "agent.enabled": ("bool", "agent_enabled"),
    "agent.scan_interval_minutes": ("int", "scan_interval"),
    "agent.max_tasks_per_day": ("int", "max_tasks_day"),
    "agent.max_task_age_days": ("int", "max_task_age"),
    "agent.require_manual_claim_approval": ("bool", "manual_claim"),
    "agent.require_manual_delivery_approval": ("bool", "manual_delivery"),
    "profitability.minimum_profit_usd": ("float", "min_profit"),
    "profitability.minimum_hourly_rate_usd": ("float", "min_hourly"),
    "profitability.minimum_success_probability": ("float", "min_probability"),
    "profitability.platform_fee_percent": ("float", "fee_percent"),
    "profitability.platform_fee_fixed_usd": ("float", "fee_fixed"),
    "profitability.infrastructure_cost_per_task_usd": ("float", "infra_cost"),
    "budget.daily_limit_usd": ("float", "daily_limit"),
    "budget.monthly_limit_usd": ("float", "monthly_limit"),
    "budget.max_ai_requests_per_day": ("int", "max_requests"),
    "execution.docker_enabled": ("bool", "docker_enabled"),
    "execution.max_execution_minutes": ("int", "max_execution"),
    "execution.max_memory_mb": ("int", "max_memory"),
    "execution.network_enabled_by_default": ("bool", "network_enabled"),
    "security.require_automation_permission": ("bool", "require_permission"),
    "security.require_task_verification": ("bool", "require_verification"),
}

SETTINGS_FORM_MAP = {
    "agent_enabled": "agent.enabled",
    "scan_interval": "agent.scan_interval_minutes",
    "max_tasks_day": "agent.max_tasks_per_day",
    "max_task_age": "agent.max_task_age_days",
    "manual_claim": "agent.require_manual_claim_approval",
    "manual_delivery": "agent.require_manual_delivery_approval",
    "min_profit": "profitability.minimum_profit_usd",
    "min_hourly": "profitability.minimum_hourly_rate_usd",
    "min_probability": "profitability.minimum_success_probability",
    "fee_percent": "profitability.platform_fee_percent",
    "fee_fixed": "profitability.platform_fee_fixed_usd",
    "infra_cost": "profitability.infrastructure_cost_per_task_usd",
    "daily_limit": "budget.daily_limit_usd",
    "monthly_limit": "budget.monthly_limit_usd",
    "max_requests": "budget.max_ai_requests_per_day",
    "docker_enabled": "execution.docker_enabled",
    "max_execution": "execution.max_execution_minutes",
    "max_memory": "execution.max_memory_mb",
    "network_enabled": "execution.network_enabled_by_default",
    "require_permission": "security.require_automation_permission",
    "require_verification": "security.require_task_verification",
}


@bp.route("/")
@login_required
def index():
    settings = get_settings()
    budget = get_budget()
    current = {key: settings.get(key) for key in GENERAL_FIELDS}
    return render_template(
        "settings/index.html",
        current=current,
        mode=settings.get("agent.mode", "observation"),
        ai_provider=settings.get("ai.provider"),
        ai_model=settings.get("ai.model"),
        ai_max_requests=settings.get_int("ai.max_requests_per_task", 10),
        ai_timeout=settings.get_int("ai.timeout_seconds", 300),
        api_key_configured=bool(settings.get_ai_api_key()),
        github_token_configured=settings.has_platform_token(),
        budget=budget.status(),
    )


@bp.post("/save")
@admin_required
def save():
    settings = get_settings()
    changed = []
    for form_name, key in SETTINGS_FORM_MAP.items():
        kind = "bool"
        for _key, (field_kind, field_name) in GENERAL_FIELDS.items():
            if field_name == form_name:
                kind = field_kind
                break
        if kind == "bool":
            value = request.form.get(form_name) == "on"
        else:
            raw = request.form.get(form_name)
            if raw is None or raw == "":
                continue
            try:
                value = int(raw) if kind == "int" else float(raw)
            except ValueError:
                flash(f"Invalid numeric value for {form_name}.", "error")
                return redirect(url_for("settings.index"))
        try:
            if settings.get(key) != value:
                settings.set(key, value)
                changed.append(key)
        except ConfigurationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings.index"))

    mode = request.form.get("mode") or settings.get("agent.mode")
    error = _validate_mode(settings, mode)
    if error:
        flash(error, "error")
        return redirect(url_for("settings.index"))
    if settings.get("agent.mode") != mode:
        settings.set("agent.mode", mode)
        settings.set("agent.observation_mode", mode == "observation")
        changed.append("agent.mode")

    if changed:
        LogService().info("Settings updated", keys=changed, actor=current_user.username)
        flash(f"Saved {len(changed)} setting(s).", "success")
    else:
        flash("No changes.", "info")
    return redirect(url_for("settings.index"))


def _validate_mode(settings, mode: str) -> str | None:
    if mode not in {"observation", "semiautomatic", "autonomous"}:
        return "Invalid operating mode."
    if mode == "autonomous":
        if not settings.get_ai_api_key():
            return "Autonomous mode requires an AI API key."
        allowed = PlatformConfig.query.filter_by(
            enabled=True, automation_permission="allowed"
        ).count()
        if allowed == 0:
            return (
                "Autonomous mode requires at least one enabled platform with verified automation permission."
            )
    return None


@bp.post("/ai")
@admin_required
def save_ai():
    settings = get_settings()
    provider = (request.form.get("ai_provider") or "opencode_go").strip()
    model = (request.form.get("ai_model") or "mixed").strip()
    api_key = (request.form.get("api_key") or "").strip()
    if provider != "opencode_go":
        flash("Only the opencode_go provider is implemented.", "error")
        return redirect(url_for("settings.index"))
    try:
        max_requests = max(1, int(request.form.get("ai_max_requests") or 10))
        timeout = max(30, int(request.form.get("ai_timeout") or 300))
    except ValueError:
        flash("Invalid numeric AI settings.", "error")
        return redirect(url_for("settings.index"))
    settings.set("ai.provider", provider)
    settings.set("ai.model", model)
    settings.set("ai.max_requests_per_task", max_requests)
    settings.set("ai.timeout_seconds", timeout)
    if api_key:
        settings.set_ai_api_key(api_key)
    LogService().info("AI settings updated", provider=provider, model=model, actor=current_user.username)
    flash("AI settings saved.", "success")
    return redirect(url_for("settings.index"))


@bp.post("/github-token")
@admin_required
def save_github_token():
    settings = get_settings()
    token = (request.form.get("github_token") or "").strip()
    if token and not token.startswith(("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")):
        flash("Token does not look like a GitHub token. It was saved anyway; verify it carefully.", "warning")
    settings.set_platform_token(token)
    LogService().info("GitHub token updated", actor=current_user.username)
    flash("GitHub token updated." if token else "GitHub token cleared.", "success")
    return redirect(url_for("settings.index"))


@bp.post("/test-ai")
@admin_required
def test_ai():
    config = get_config()
    settings = get_settings()
    budget = get_budget()
    try:
        provider = build_ai_provider(config, settings, budget)
        ok, message = provider.test_connection()
        LogService().info(
            "AI connection test",
            ok=ok,
            detail=message[:300],
            actor=current_user.username,
        )
        flash(f"AI connection: {message}", "success" if ok else "error")
    except Exception as exc:
        flash(f"AI connection test failed: {exc}", "error")
    return redirect(url_for("settings.index"))
