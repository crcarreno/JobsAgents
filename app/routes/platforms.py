import json
import re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from core.database import db
from app.adapters import adapter_names, build_adapter
from app.models import AutomationPermission, PlatformConfig
from app.routes.helpers import admin_required, get_config, get_settings
from app.services.discovery_service import DiscoveryService
from app.services.log_service import LogService

bp = Blueprint("platforms", __name__, url_prefix="/platforms")
SLUG_PATTERN = re.compile(r"^[a-z0-9_]{2,80}$")


def _parse_config(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Platform configuration must be a JSON object")
    return data


@bp.route("/")
@login_required
def index():
    platforms = PlatformConfig.query.order_by(PlatformConfig.name.asc()).all()
    settings = get_settings()
    return render_template(
        "platforms/index.html",
        platforms=platforms,
        adapters=adapter_names(),
        github_token_configured=settings.has_platform_token(),
    )


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def new():
    if request.method == "POST":
        return _save(None)
    return render_template(
        "platforms/form.html",
        platform=None,
        adapters=adapter_names(),
        config_json="{}",
        automation_permissions=AutomationPermission.ALL,
    )


@bp.route("/<int:platform_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(platform_id):
    platform = db.session.get(PlatformConfig, platform_id) or abort(404)
    if request.method == "POST":
        return _save(platform)
    return render_template(
        "platforms/form.html",
        platform=platform,
        adapters=adapter_names(),
        config_json=json.dumps(platform.config or {}, indent=2),
        automation_permissions=AutomationPermission.ALL,
    )


def _save(platform: PlatformConfig | None):
    name = (request.form.get("name") or "").strip()
    slug = (request.form.get("slug") or "").strip().lower()
    adapter = (request.form.get("adapter") or "").strip()
    permission = request.form.get("automation_permission") or "unknown"
    notes = (request.form.get("automation_permission_notes") or "").strip()
    interval = request.form.get("scan_interval_minutes") or "60"
    enabled = request.form.get("enabled") == "on"

    if not name:
        flash("Name is required.", "error")
        return redirect(request.url)
    if not SLUG_PATTERN.match(slug):
        flash("Slug must use lowercase letters, numbers and underscores (2-80 chars).", "error")
        return redirect(request.url)
    if adapter not in adapter_names():
        flash("Unknown adapter.", "error")
        return redirect(request.url)
    if permission not in AutomationPermission.ALL:
        flash("Invalid automation permission value.", "error")
        return redirect(request.url)
    if permission == "allowed" and not notes:
        flash("You must document why automation is explicitly allowed before marking it allowed.", "error")
        return redirect(request.url)
    try:
        interval_value = max(1, int(interval))
    except ValueError:
        interval_value = 60
    try:
        config_data = _parse_config(request.form.get("config_json") or "{}")
    except (json.JSONDecodeError, ValueError) as exc:
        flash(f"Invalid JSON configuration: {exc}", "error")
        return redirect(request.url)

    existing = PlatformConfig.query.filter_by(slug=slug).first()
    if existing and (platform is None or existing.id != platform.id):
        flash("Slug is already in use.", "error")
        return redirect(request.url)

    if platform is None:
        platform = PlatformConfig(name=name, slug=slug, adapter=adapter)
        db.session.add(platform)
    platform.name = name
    platform.slug = slug
    platform.adapter = adapter
    platform.automation_permission = permission
    platform.automation_permission_notes = notes or None
    platform.scan_interval_minutes = interval_value
    platform.enabled = enabled
    platform.config = config_data
    db.session.commit()
    LogService().info(
        "Platform configuration saved",
        platform=slug,
        enabled=enabled,
        permission=permission,
        actor=current_user.username,
    )
    flash("Platform saved.", "success")
    return redirect(url_for("platforms.index"))


@bp.route("/<int:platform_id>/toggle", methods=["POST"])
@admin_required
def toggle(platform_id):
    platform = db.session.get(PlatformConfig, platform_id) or abort(404)
    platform.enabled = not platform.enabled
    db.session.commit()
    flash(f"Platform {'enabled' if platform.enabled else 'disabled'}.", "success")
    return redirect(url_for("platforms.index"))


@bp.route("/<int:platform_id>/test", methods=["POST"])
@admin_required
def test(platform_id):
    platform = db.session.get(PlatformConfig, platform_id) or abort(404)
    try:
        adapter = build_adapter(platform, get_config(), get_settings())
        connection = adapter.connect()
        platform.status = connection.status
        platform.last_scan_message = connection.message[:2000]
        db.session.commit()
        flash(f"Connection: {connection.status} — {connection.message}", "success" if connection.ok else "warning")
    except Exception as exc:
        flash(f"Connection test failed: {exc}", "error")
    return redirect(url_for("platforms.index"))


@bp.route("/<int:platform_id>/scan", methods=["POST"])
@admin_required
def scan(platform_id):
    platform = db.session.get(PlatformConfig, platform_id) or abort(404)
    try:
        result = DiscoveryService(get_config(), get_settings()).scan_platform(platform)
        flash(f"Scan finished: {result.get('new_tasks', 0)} new task(s).", "success")
    except Exception as exc:
        flash(f"Scan failed: {exc}", "error")
    return redirect(url_for("platforms.index"))
