from datetime import timedelta

from flask import Blueprint, render_template
from flask_login import login_required
from sqlalchemy import func

from core.database import db
from core.timeutil import utcnow
from app.models import Earning, EarningStatus, PlatformConfig, SystemLog, Task, TaskStatus, WorkerState
from app.routes.helpers import get_budget, get_settings

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_required
def index():
    settings = get_settings()
    budget = get_budget()
    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    status_counts = dict(
        db.session.query(Task.status, func.count(Task.id)).group_by(Task.status).all()
    )
    total_tasks = sum(status_counts.values())
    platforms = PlatformConfig.query.order_by(PlatformConfig.name.asc()).all()

    income_total = float(
        db.session.query(func.coalesce(func.sum(Earning.amount), 0))
        .filter(Earning.status == EarningStatus.PAID)
        .scalar()
        or 0
    )
    income_today = float(
        db.session.query(func.coalesce(func.sum(Earning.amount), 0))
        .filter(Earning.status == EarningStatus.PAID, Earning.paid_at >= today_start)
        .scalar()
        or 0
    )
    income_month = float(
        db.session.query(func.coalesce(func.sum(Earning.amount), 0))
        .filter(Earning.status == EarningStatus.PAID, Earning.paid_at >= month_start)
        .scalar()
        or 0
    )

    series = []
    for offset in range(6, -1, -1):
        day = (today_start - timedelta(days=offset)).date()
        value = float(
            db.session.query(func.coalesce(func.sum(Earning.amount), 0))
            .filter(
                Earning.status == EarningStatus.PAID,
                func.date(Earning.paid_at) == day,
            )
            .scalar()
            or 0
        )
        series.append({"day": day.strftime("%a"), "value": value})
    max_series = max([item["value"] for item in series] + [0.01])

    last_scan = (
        db.session.query(func.max(PlatformConfig.last_scan_at)).scalar()
    )
    due_platforms = [platform for platform in platforms if platform.enabled]
    next_scan_candidates = []
    for platform in due_platforms:
        if platform.last_scan_at is None:
            next_scan_candidates.append(now)
        else:
            next_scan_candidates.append(
                platform.last_scan_at + timedelta(minutes=platform.scan_interval_minutes)
            )
    next_scan = min(next_scan_candidates) if next_scan_candidates else None

    worker = db.session.get(WorkerState, 1)
    recent_errors = (
        SystemLog.query.filter(SystemLog.level == "ERROR").order_by(SystemLog.created_at.desc()).limit(6).all()
    )
    recent_tasks = Task.query.order_by(Task.created_at.desc()).limit(8).all()
    outcomes = {
        "found": total_tasks,
        "approved": status_counts.get(TaskStatus.APPROVED, 0) + status_counts.get(TaskStatus.EXECUTING, 0),
        "executed": status_counts.get(TaskStatus.EXECUTING, 0)
        + status_counts.get(TaskStatus.COMPLETED, 0)
        + status_counts.get(TaskStatus.READY_TO_SUBMIT, 0)
        + status_counts.get(TaskStatus.SUBMITTED, 0)
        + status_counts.get(TaskStatus.ACCEPTED, 0),
        "delivered": status_counts.get(TaskStatus.SUBMITTED, 0) + status_counts.get(TaskStatus.ACCEPTED, 0),
        "accepted": status_counts.get(TaskStatus.ACCEPTED, 0),
        "rejected": status_counts.get(TaskStatus.REJECTED, 0),
    }
    budget_status = budget.status()
    ai_spend = budget_status["spent_month"]
    net_estimated = income_month - ai_spend

    platform_profitability = []
    for platform in platforms:
        productive_statuses = [
            TaskStatus.EXECUTING,
            TaskStatus.COMPLETED,
            TaskStatus.READY_TO_SUBMIT,
            TaskStatus.SUBMITTED,
            TaskStatus.ACCEPTED,
        ]
        productive = Task.query.filter(
            Task.platform_id == platform.id, Task.status.in_(productive_statuses)
        ).count()
        estimated_net = float(
            db.session.query(func.coalesce(func.sum(Task.net_profit), 0))
            .filter(Task.platform_id == platform.id, Task.status.in_(productive_statuses))
            .scalar()
            or 0
        )
        paid = float(
            db.session.query(func.coalesce(func.sum(Earning.amount), 0))
            .filter(Earning.platform_id == platform.id, Earning.status == EarningStatus.PAID)
            .scalar()
            or 0
        )
        platform_profitability.append(
            {
                "platform": platform,
                "productive_tasks": productive,
                "estimated_net": estimated_net,
                "paid": paid,
            }
        )

    return render_template(
        "dashboard/index.html",
        status_counts=status_counts,
        total_tasks=total_tasks,
        platforms=platforms,
        outcomes=outcomes,
        income_total=income_total,
        income_today=income_today,
        income_month=income_month,
        ai_spend=ai_spend,
        net_estimated=net_estimated,
        budget=budget_status,
        series=series,
        max_series=max_series,
        last_scan=last_scan,
        next_scan=next_scan,
        worker=worker,
        recent_errors=recent_errors,
        recent_tasks=recent_tasks,
        platform_profitability=platform_profitability,
        mode=settings.get("agent.mode", "observation"),
        agent_enabled=settings.get_bool("agent.enabled", True),
    )
