from functools import wraps

from flask import abort, current_app
from flask_login import current_user

from app.services.budget_service import BudgetService
from app.services.settings_service import SettingsService


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def get_config():
    return current_app.extensions["jobagents_config"]


def get_settings() -> SettingsService:
    return SettingsService(get_config())


def get_budget() -> BudgetService:
    return BudgetService(get_settings())


def int_or(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_or(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
