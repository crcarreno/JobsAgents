from core.database import db
from core.exceptions import TaskStateError
from core.timeutil import utcnow
from app.models import Task, TaskStatus, TaskStatusHistory

ALLOWED_TRANSITIONS = {
    TaskStatus.PENDING: {
        TaskStatus.EVALUATING,
        TaskStatus.APPROVED,
        TaskStatus.REJECTED,
        TaskStatus.EXPIRED,
        TaskStatus.FAILED,
    },
    TaskStatus.EVALUATING: {
        TaskStatus.APPROVED,
        TaskStatus.REJECTED,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.FAILED,
        TaskStatus.EXPIRED,
    },
    TaskStatus.APPROVED: {
        TaskStatus.EXECUTING,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.EXPIRED,
        TaskStatus.FAILED,
        TaskStatus.REJECTED,
    },
    TaskStatus.EXECUTING: {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.EXPIRED,
    },
    TaskStatus.COMPLETED: {TaskStatus.READY_TO_SUBMIT, TaskStatus.MANUAL_REVIEW, TaskStatus.FAILED},
    TaskStatus.READY_TO_SUBMIT: {
        TaskStatus.SUBMITTED,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.FAILED,
        TaskStatus.EXPIRED,
    },
    TaskStatus.SUBMITTED: {TaskStatus.ACCEPTED, TaskStatus.REJECTED, TaskStatus.MANUAL_REVIEW, TaskStatus.FAILED},
    TaskStatus.MANUAL_REVIEW: {
        TaskStatus.EVALUATING,
        TaskStatus.APPROVED,
        TaskStatus.EXECUTING,
        TaskStatus.READY_TO_SUBMIT,
        TaskStatus.SUBMITTED,
        TaskStatus.REJECTED,
        TaskStatus.FAILED,
        TaskStatus.EXPIRED,
    },
    TaskStatus.ACCEPTED: set(),
    TaskStatus.REJECTED: {
        TaskStatus.PENDING,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.APPROVED,
        TaskStatus.EVALUATING,
    },
    TaskStatus.FAILED: {
        TaskStatus.PENDING,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.APPROVED,
        TaskStatus.EVALUATING,
    },
    TaskStatus.EXPIRED: set(),
}


def transition(task: Task, to_status: str, reason: str | None = None, actor: str = "system", commit: bool = True):
    if to_status not in TaskStatus.ALL:
        raise TaskStateError(f"Unknown task status: {to_status}")
    from_status = task.status
    if from_status == to_status:
        return task
    allowed = ALLOWED_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise TaskStateError(f"Invalid transition {from_status} -> {to_status} for task {task.id}")
    task.status = to_status
    task.last_reason = reason
    if to_status == TaskStatus.SUBMITTED:
        task.submitted_at = utcnow()
    if to_status == TaskStatus.ACCEPTED:
        task.accepted_at = utcnow()
    history = TaskStatusHistory(
        task_id=task.id,
        from_status=from_status,
        to_status=to_status,
        reason=(reason or "")[:4000] or None,
        actor=actor,
    )
    db.session.add(history)
    if commit:
        db.session.commit()
    return task
