from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from core.database import db
from core.exceptions import JobAgentsError
from core.timeutil import utcnow
from app.models import PlatformConfig, Task, TaskExecution, TaskStatus, TaskStatusHistory
from app.routes.helpers import admin_required, get_config, get_settings
from app.services.budget_service import BudgetService
from app.services.delivery_service import DeliveryService
from app.services.evaluator_service import EvaluatorService
from app.services.execution_service import ExecutionService
from app.services.task_service import transition

bp = Blueprint("tasks", __name__, url_prefix="/tasks")

STATUS_FILTERS = TaskStatus.ALL


@bp.route("/")
@login_required
def index():
    status = request.args.get("status", "")
    platform_id = request.args.get("platform", type=int)
    query_text = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", type=int) or 1)
    per_page = 25
    query = Task.query
    if status in TaskStatus.ALL:
        query = query.filter(Task.status == status)
    if platform_id:
        query = query.filter(Task.platform_id == platform_id)
    if query_text:
        like = f"%{query_text}%"
        query = query.filter(or_(Task.title.ilike(like), Task.external_id.ilike(like)))
    total = query.count()
    tasks = (
        query.order_by(Task.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    platforms = PlatformConfig.query.order_by(PlatformConfig.name.asc()).all()
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "tasks/index.html",
        tasks=tasks,
        platforms=platforms,
        statuses=STATUS_FILTERS,
        selected_status=status,
        selected_platform=platform_id,
        query_text=query_text,
        page=page,
        pages=pages,
        total=total,
    )


@bp.route("/<int:task_id>")
@login_required
def detail(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    executions = (
        TaskExecution.query.filter_by(task_id=task.id).order_by(TaskExecution.attempt.desc()).all()
    )
    history = (
        TaskStatusHistory.query.filter_by(task_id=task.id).order_by(TaskStatusHistory.created_at.desc()).all()
    )
    return render_template("tasks/detail.html", task=task, executions=executions, history=history)


def _services():
    config = get_config()
    settings = get_settings()
    budget = BudgetService(settings)
    return config, settings, budget


@bp.route("/<int:task_id>/evaluate", methods=["POST"])
@admin_required
def evaluate(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    config, settings, budget = _services()
    try:
        outcome = EvaluatorService(config, settings, budget).evaluate_task(task)
        flash(f"Evaluation finished: {outcome.status} ({outcome.reason}).", "success")
    except JobAgentsError as exc:
        flash(f"Evaluation failed: {exc}", "error")
    except Exception as exc:
        flash(f"Evaluation failed: {exc}", "error")
    return redirect(url_for("tasks.detail", task_id=task_id))


@bp.route("/<int:task_id>/approve", methods=["POST"])
@admin_required
def approve(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    if task.status not in {TaskStatus.MANUAL_REVIEW, TaskStatus.PENDING, TaskStatus.REJECTED}:
        flash("Task cannot be approved from its current status.", "error")
        return redirect(url_for("tasks.detail", task_id=task_id))
    if task.automation_allowed is False:
        flash("Automation was explicitly denied for this task; it cannot be approved.", "error")
        return redirect(url_for("tasks.detail", task_id=task_id))
    confirm = request.form.get("confirm_automation") == "on"
    if task.automation_allowed is not True:
        if not confirm:
            flash("You must confirm that automation is allowed before approving this task.", "error")
            return redirect(url_for("tasks.detail", task_id=task_id))
        task.automation_allowed = True
        evaluation = task.evaluation or {}
        automation = evaluation.get("automation") or {}
        automation["manual_override"] = {
            "actor": current_user.username,
            "at": utcnow().isoformat(),
            "notes": request.form.get("notes") or "",
        }
        evaluation["automation"] = automation
        task.evaluation = evaluation
        task.automation_notes = (
            (task.automation_notes or "") + " | Manually confirmed by " + current_user.username
        )
    transition(task, TaskStatus.APPROVED, "admin approved the task", actor=current_user.username)
    flash("Task approved.", "success")
    return redirect(url_for("tasks.detail", task_id=task_id))


@bp.route("/<int:task_id>/claim", methods=["POST"])
@admin_required
def claim(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    config, settings, budget = _services()
    try:
        result = ExecutionService(config, settings, budget).claim(task, actor=current_user.username)
        flash(f"Claim result: {result.get('status')}.", "success")
    except Exception as exc:
        flash(f"Claim failed: {exc}", "error")
    return redirect(url_for("tasks.detail", task_id=task_id))


@bp.route("/<int:task_id>/execute", methods=["POST"])
@admin_required
def execute(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    config, settings, budget = _services()
    try:
        execution = ExecutionService(config, settings, budget).execute(
            task, actor=current_user.username, manual=True
        )
        flash(f"Execution finished with status {execution.status}.", "success")
    except Exception as exc:
        flash(f"Execution failed: {exc}", "error")
    return redirect(url_for("tasks.detail", task_id=task_id))


@bp.route("/<int:task_id>/deliver", methods=["POST"])
@admin_required
def deliver(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    config, settings, _budget = _services()
    try:
        result = DeliveryService(config, settings).deliver(task, actor=current_user.username, manual=True)
        flash(f"Delivery: {result.get('status')} {result.get('url') or ''}", "success")
    except Exception as exc:
        flash(f"Delivery failed: {exc}", "error")
    return redirect(url_for("tasks.detail", task_id=task_id))


@bp.route("/<int:task_id>/refresh", methods=["POST"])
@admin_required
def refresh(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    config, settings, _budget = _services()
    try:
        status = DeliveryService(config, settings).refresh_status(task)
        flash(f"Platform status: {status.get('status')}.", "success")
    except Exception as exc:
        flash(f"Status refresh failed: {exc}", "error")
    return redirect(url_for("tasks.detail", task_id=task_id))


@bp.route("/<int:task_id>/reject", methods=["POST"])
@admin_required
def reject(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    reason = (request.form.get("reason") or "rejected by administrator").strip()
    if task.status == TaskStatus.SUBMITTED:
        transition(task, TaskStatus.REJECTED, reason, actor=current_user.username)
        flash("Task marked as rejected.", "success")
    elif task.status in {TaskStatus.PENDING, TaskStatus.MANUAL_REVIEW, TaskStatus.APPROVED, TaskStatus.EVALUATING}:
        transition(task, TaskStatus.REJECTED, reason, actor=current_user.username)
        flash("Task rejected.", "success")
    else:
        flash("Task cannot be rejected from its current status.", "error")
    return redirect(url_for("tasks.detail", task_id=task_id))
