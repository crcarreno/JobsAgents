from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from core.database import db
from core.timeutil import utcnow
from app.models import Earning, EarningStatus, PlatformConfig, Task, TaskStatus
from app.routes.helpers import admin_required
from app.services.task_service import transition

bp = Blueprint("earnings", __name__, url_prefix="/earnings")


@bp.route("/")
@login_required
def index():
    page = max(1, request.args.get("page", type=int) or 1)
    per_page = 30
    query = Earning.query.order_by(Earning.created_at.desc())
    total = query.count()
    earnings = query.offset((page - 1) * per_page).limit(per_page).all()
    totals = dict(
        db.session.query(Earning.status, func.coalesce(func.sum(Earning.amount), 0))
        .group_by(Earning.status)
        .all()
    )
    platforms = PlatformConfig.query.order_by(PlatformConfig.name.asc()).all()
    tasks = (
        Task.query.filter(Task.status.in_([TaskStatus.SUBMITTED, TaskStatus.ACCEPTED]))
        .order_by(Task.created_at.desc())
        .limit(200)
        .all()
    )
    return render_template(
        "earnings/index.html",
        earnings=earnings,
        page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        total=total,
        totals=totals,
        platforms=platforms,
        tasks=tasks,
    )


@bp.post("/add")
@admin_required
def add():
    try:
        amount = float(request.form.get("amount") or 0)
    except ValueError:
        flash("Invalid amount.", "error")
        return redirect(url_for("earnings.index"))
    if amount <= 0:
        flash("Amount must be greater than zero.", "error")
        return redirect(url_for("earnings.index"))
    status = request.form.get("status") or EarningStatus.PENDING
    if status not in {EarningStatus.PENDING, EarningStatus.PAID, EarningStatus.FAILED, EarningStatus.VOID}:
        status = EarningStatus.PENDING
    task_id = request.form.get("task_id", type=int)
    platform_id = request.form.get("platform_id", type=int)
    task = db.session.get(Task, task_id) if task_id else None
    earning = Earning(
        task_id=task.id if task else None,
        platform_id=platform_id or (task.platform_id if task else None),
        amount=amount,
        currency=(request.form.get("currency") or "USD").upper()[:8],
        status=status,
        reference=(request.form.get("reference") or "").strip()[:255] or None,
        notes=(request.form.get("notes") or "").strip()[:4000] or None,
        paid_at=utcnow() if status == EarningStatus.PAID else None,
    )
    db.session.add(earning)
    db.session.commit()
    if task and status == EarningStatus.PAID and task.status == TaskStatus.SUBMITTED:
        transition(task, TaskStatus.ACCEPTED, "earning registered as paid", actor=current_user.username)
    flash("Earning recorded.", "success")
    return redirect(url_for("earnings.index"))


@bp.post("/<int:earning_id>/paid")
@admin_required
def mark_paid(earning_id):
    earning = db.session.get(Earning, earning_id) or abort(404)
    earning.status = EarningStatus.PAID
    earning.paid_at = earning.paid_at or utcnow()
    db.session.commit()
    if earning.task and earning.task.status == TaskStatus.SUBMITTED:
        transition(earning.task, TaskStatus.ACCEPTED, "earning marked as paid", actor=current_user.username)
    flash("Earning marked as paid.", "success")
    return redirect(url_for("earnings.index"))
