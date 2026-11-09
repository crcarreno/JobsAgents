import shutil
from pathlib import Path

from core.database import db
from core.exceptions import AutomationNotAllowedError, DeliveryError, TaskStateError
from core.timeutil import utcnow
from app.adapters import build_adapter
from app.models import Task, TaskExecution, TaskStatus
from app.services.artifact_service import load_artifact_contents
from app.services.log_service import LogService
from app.services.settings_service import SettingsService
from app.services.task_service import transition


class DeliveryService:
    def __init__(self, config, settings: SettingsService):
        self.config = config
        self.settings = settings
        self.log = LogService()

    def deliver(self, task: Task, actor: str = "worker", manual: bool = False) -> dict:
        task = db.session.get(Task, task.id)
        if task is None:
            raise TaskStateError("Task not found")
        if task.delivery_reference or task.submitted_at:
            return {"status": "already_submitted", "reference": task.delivery_reference}
        if task.status not in {TaskStatus.READY_TO_SUBMIT, TaskStatus.MANUAL_REVIEW}:
            raise TaskStateError(f"Task cannot be delivered from status {task.status}")
        if task.automation_allowed is not True:
            raise AutomationNotAllowedError("Automation permission has not been verified for this task")
        if self.settings.get_bool("security.require_automation_permission", True):
            if task.platform.automation_permission != "allowed":
                raise AutomationNotAllowedError("Platform automation permission is not confirmed")
        mode = self.settings.get("agent.mode", "observation")
        if not manual:
            if mode == "observation":
                raise TaskStateError("Delivery is disabled in observation mode")
            if mode == "semiautomatic" and self.settings.get_bool(
                "agent.require_manual_delivery_approval", True
            ):
                raise TaskStateError("Delivery requires manual approval in semiautomatic mode")

        execution = (
            TaskExecution.query.filter_by(task_id=task.id)
            .filter(TaskExecution.status.in_(["succeeded", "blocked"]))
            .order_by(TaskExecution.attempt.desc())
            .first()
        )
        if execution is None or execution.status != "succeeded":
            raise DeliveryError("No successful execution is available for delivery")
        artifacts = load_artifact_contents(execution.artifacts)
        if not artifacts:
            raise DeliveryError("No artifacts available for delivery")

        updated = (
            db.session.query(Task)
            .filter(Task.id == task.id, Task.submitted_at.is_(None))
            .update({"submitted_at": utcnow()}, synchronize_session=False)
        )
        db.session.commit()
        if not updated:
            db.session.refresh(task)
            return {"status": "already_submitted", "reference": task.delivery_reference}

        adapter = build_adapter(task.platform, self.config, self.settings)
        summary = execution.output_summary or task.title
        try:
            result = adapter.submit_task(task, artifacts, summary=summary)
        except Exception as exc:
            task = db.session.get(Task, task.id)
            task.submitted_at = None
            db.session.commit()
            transition(task, TaskStatus.MANUAL_REVIEW, f"delivery failed: {exc}", actor=actor)
            self.log.error("Task delivery failed", task_id=task.id, error=str(exc)[:1000])
            raise
        task = db.session.get(Task, task.id)
        task.delivery_reference = result.reference or result.url
        evaluation = task.evaluation or {}
        evaluation["delivery"] = {
            "reference": result.reference,
            "url": result.url,
            "status": result.status,
            "message": result.message,
        }
        task.evaluation = evaluation
        db.session.commit()
        transition(task, TaskStatus.SUBMITTED, f"delivered: {result.message}", actor=actor)
        self._cleanup_workspace(execution)
        self.log.info("Task delivered", task_id=task.id, reference=result.reference, url=result.url)
        return {"status": result.status, "reference": result.reference, "url": result.url}

    def _cleanup_workspace(self, execution: TaskExecution) -> None:
        if not execution.workspace_path:
            return
        try:
            root = self.config.resolve_path(
                self.settings.get("execution.workspace_root", "var/workspaces")
            ).resolve()
            workspace = Path(execution.workspace_path).resolve()
            if workspace != root and root in workspace.parents and workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)
        except OSError:
            pass

    def refresh_status(self, task: Task) -> dict:
        task = db.session.get(Task, task.id)
        if not task.delivery_reference and task.status != TaskStatus.SUBMITTED:
            return {"status": "not_submitted"}
        adapter = build_adapter(task.platform, self.config, self.settings)
        try:
            status = adapter.check_task_status(task)
        except Exception as exc:
            self.log.warning("Status refresh failed", task_id=task.id, error=str(exc)[:500])
            return {"status": "error", "message": str(exc)[:500]}
        status_value = status.get("status")
        if status_value == "accepted" and task.status == TaskStatus.SUBMITTED:
            transition(task, TaskStatus.ACCEPTED, "platform reported the contribution as accepted", actor="worker")
        elif status_value == "rejected" and task.status == TaskStatus.SUBMITTED:
            transition(task, TaskStatus.REJECTED, "platform reported the contribution as rejected", actor="worker")
        return status
