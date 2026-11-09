import time
from collections import defaultdict, deque

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from core.database import db
from core.security import verify_password
from core.timeutil import utcnow
from app.models import User
from app.routes.helpers import get_settings
from app.services.log_service import LogService

bp = Blueprint("auth", __name__)

_attempts: dict[str, deque] = defaultdict(deque)


def _client_key() -> str:
    return request.remote_addr or "unknown"


def _too_many_attempts(key: str, max_attempts: int, window_seconds: int) -> bool:
    now = time.time()
    bucket = _attempts[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    return len(bucket) >= max_attempts


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    settings = get_settings()
    max_attempts = settings.get_int("security.login_max_attempts", 5)
    lockout_minutes = settings.get_int("security.login_lockout_minutes", 15)
    window = lockout_minutes * 60
    key = _client_key()
    if _too_many_attempts(key, max_attempts, window):
        flash("Too many login attempts. Try again later.", "error")
        return render_template("auth/login.html"), 429

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        now = utcnow()
        if user and user.locked_until and user.locked_until > now:
            flash("Account temporarily locked. Try again later.", "error")
            return render_template("auth/login.html"), 429
        if user and user.is_active and verify_password(user.password_hash, password):
            user.failed_login_attempts = 0
            user.locked_until = None
            user.last_login_at = now
            db.session.commit()
            session.clear()
            session.permanent = True
            login_user(user)
            LogService().info("Admin login succeeded", username=user.username, ip=request.remote_addr)
            next_url = request.args.get("next") or url_for("dashboard.index")
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = url_for("dashboard.index")
            return redirect(next_url)
        _attempts[key].append(time.time())
        if user:
            user.failed_login_attempts = int(user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= max_attempts:
                from datetime import timedelta

                user.locked_until = now + timedelta(minutes=lockout_minutes)
            db.session.commit()
        LogService().warning("Admin login failed", username=username[:64], ip=request.remote_addr)
        flash("Invalid credentials.", "error")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    if current_user.is_authenticated:
        LogService().info("Admin logout", username=current_user.username)
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))
