from app.models import Task
from app.providers.base import TaskContext


def build_task_context(task: Task, evaluation: dict | None = None) -> TaskContext:
    evaluation = evaluation if evaluation is not None else (task.evaluation or {})
    extra = {
        "repo": (task.raw_payload or {}).get("repo") or evaluation.get("repo"),
        "labels": (task.raw_payload or {}).get("labels", []),
        "default_branch": evaluation.get("default_branch"),
        "automation_status": evaluation.get("automation", {}).get("status"),
    }
    return TaskContext(
        task_id=task.id,
        platform=task.platform.slug if task.platform else "unknown",
        external_id=task.external_id,
        title=task.title,
        description=task.description,
        url=task.url,
        reward=float(task.reward_amount or 0),
        currency=task.currency or "USD",
        deadline=task.deadline_at.isoformat() if task.deadline_at else None,
        expected_output=evaluation.get("expected_output"),
        language=evaluation.get("language"),
        automation_notes=task.automation_notes,
        extra=extra,
    )
