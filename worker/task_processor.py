import os
from datetime import datetime, timedelta

from flask import Flask

from core.database import db
from core.exceptions import AIBudgetExceeded, AILimitExceeded, JobAgentsError
from core.logger import get_logger
from core.timeutil import utcnow
from app.models import Task, TaskStatus, WorkerState, WorkerStatus
from app.services.budget_service import BudgetService
from app.services.delivery_service import DeliveryService
from app.services.discovery_service import DiscoveryService
from app.services.evaluator_service import EvaluatorService
from app.services.execution_service import ExecutionService
from app.services.log_service import LogService
from app.services.settings_service import SettingsService


class TaskProcessor:
    def __init__(self, app: Flask):
        self.app = app
        self.config = app.extensions["jobagents_config"]
        self.log = LogService()
        self.logger = get_logger("worker")

    def _state(self) -> WorkerState:
        state = db.session.get(WorkerState, 1)
        if state is None:
            state = WorkerState(id=1, status=WorkerStatus.STOPPED)
            db.session.add(state)
            db.session.commit()
        return state

    def update_state(self, **fields) -> None:
        state = self._state()
        for key, value in fields.items():
            setattr(state, key, value)
        state.pid = os.getpid()
        state.last_heartbeat = utcnow()
        db.session.commit()

    def run_cycle(self) -> dict:
        with self.app.app_context():
            settings = SettingsService(self.config)
            budget = BudgetService(settings)
            summary = {"started_at": utcnow().isoformat()}
            self.update_state(status=WorkerStatus.SCANNING, last_scan_started_at=utcnow(), message="scanning")
            try:
                self._expire_old_tasks(settings)
                if not settings.get_bool("agent.enabled", True):
                    self.update_state(status=WorkerStatus.RUNNING, message="agent disabled")
                    summary["skipped"] = "agent disabled"
                    return summary

                discovery = DiscoveryService(self.config, settings)
                scan_summary = discovery.scan_due_platforms()
                summary["scan"] = scan_summary
                self.update_state(
                    last_scan_finished_at=utcnow(),
                    next_scan_at=utcnow() + timedelta(minutes=settings.get_int("agent.scan_interval_minutes", 30)),
                    status=WorkerStatus.PROCESSING,
                    message="evaluating",
                )

                evaluator = EvaluatorService(self.config, settings, budget)
                outcomes = evaluator.evaluate_pending(limit=settings.get_int("agent.max_tasks_per_day", 10) * 2)
                summary["evaluated"] = [
                    {"task_id": outcome.task_id, "status": outcome.status} for outcome in outcomes
                ]

                mode = settings.get("agent.mode", "observation")
                summary["mode"] = mode
                if mode in {"semiautomatic", "autonomous"}:
                    summary["executions"] = self._execute_approved(settings, budget, mode)
                if mode == "autonomous":
                    summary["deliveries"] = self._deliver_ready(settings)
                summary["status_refresh"] = self._refresh_submitted(settings)
                self.update_state(status=WorkerStatus.RUNNING, current_task_id=None, message="idle")
                summary["finished_at"] = utcnow().isoformat()
                self.log.info("Worker cycle finished", summary=summary)
                return summary
            except (AIBudgetExceeded, AILimitExceeded) as exc:
                self.update_state(status=WorkerStatus.RUNNING, message=f"budget limit: {exc}")
                summary["error"] = str(exc)
                return summary
            except Exception as exc:
                db.session.rollback()
                self.update_state(status=WorkerStatus.ERROR, message=str(exc)[:1000])
                self.logger.exception("Worker cycle failed")
                summary["error"] = str(exc)
                return summary

    def _expire_old_tasks(self, settings: SettingsService) -> None:
        days = settings.get_int("agent.max_task_age_days", 30)
        if days <= 0:
            return
        cutoff = utcnow() - timedelta(days=days)
        stale = Task.query.filter(
            Task.status.in_([TaskStatus.PENDING, TaskStatus.EVALUATING, TaskStatus.APPROVED]),
            Task.created_at < cutoff,
        ).all()
        from app.services.task_service import transition

        for task in stale:
            try:
                transition(task, TaskStatus.EXPIRED, f"task older than {days} days", actor="worker")
            except JobAgentsError:
                db.session.rollback()

    def _execute_approved(self, settings: SettingsService, budget: BudgetService, mode: str) -> list[dict]:
        service = ExecutionService(self.config, settings, budget)
        results = []
        limit = settings.get_int("agent.max_tasks_per_day", 10)
        tasks = (
            Task.query.filter(Task.status == TaskStatus.APPROVED, Task.automation_allowed.is_(True))
            .order_by(Task.net_profit.desc(), Task.created_at.asc())
            .limit(limit)
            .all()
        )
        for task in tasks:
            try:
                self.update_state(status=WorkerStatus.PROCESSING, current_task_id=task.id, message=f"executing task {task.id}")
                if mode == "autonomous" and task.claimed_at is None:
                    try:
                        self.log.info("Claiming task before execution", task_id=task.id)
                        service.claim(task, actor="worker")
                    except JobAgentsError as exc:
                        self.log.warning("Claim skipped", task_id=task.id, error=str(exc)[:500])
                execution = service.execute(task, actor="worker")
                results.append({"task_id": task.id, "execution_id": execution.id, "status": execution.status})
            except (AIBudgetExceeded, AILimitExceeded) as exc:
                results.append({"task_id": task.id, "error": str(exc)})
                break
            except JobAgentsError as exc:
                results.append({"task_id": task.id, "error": str(exc)[:500]})
        return results

    def _deliver_ready(self, settings: SettingsService) -> list[dict]:
        service = DeliveryService(self.config, settings)
        results = []
        tasks = (
            Task.query.filter(
                Task.status == TaskStatus.READY_TO_SUBMIT,
                Task.automation_allowed.is_(True),
            )
            .order_by(Task.net_profit.desc())
            .limit(3)
            .all()
        )
        for task in tasks:
            platform = task.platform
            if platform is None or platform.automation_permission != "allowed":
                continue
            try:
                self.update_state(status=WorkerStatus.PROCESSING, current_task_id=task.id, message=f"delivering task {task.id}")
                result = service.deliver(task, actor="worker")
                results.append({"task_id": task.id, "status": result.get("status")})
            except JobAgentsError as exc:
                results.append({"task_id": task.id, "error": str(exc)[:500]})
        return results

    def _refresh_submitted(self, settings: SettingsService) -> list[dict]:
        service = DeliveryService(self.config, settings)
        results = []
        tasks = (
            Task.query.filter(Task.status == TaskStatus.SUBMITTED)
            .order_by(Task.submitted_at.asc())
            .limit(5)
            .all()
        )
        for task in tasks:
            try:
                status = service.refresh_status(task)
                results.append({"task_id": task.id, "status": status.get("status")})
            except Exception as exc:
                results.append({"task_id": task.id, "error": str(exc)[:300]})
        return results
