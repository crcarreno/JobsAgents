from datetime import datetime

from sqlalchemy import case, func

from core.database import db
from core.exceptions import (
    AIBudgetExceeded,
    AILimitExceeded,
    AutomationNotAllowedError,
    ExecutionError,
    PlatformError,
    SandboxUnavailableError,
    TaskStateError,
)
from core.timeutil import utcnow
from app.adapters import build_adapter
from app.models import AIUsage, Task, TaskExecution, TaskStatus
from app.providers import build_ai_provider
from app.services.artifact_service import build_artifact_records
from app.services.budget_service import BudgetService
from app.services.log_service import LogService
from app.services.settings_service import SettingsService
from app.services.task_context import build_task_context
from app.services.task_service import transition
from app.services.verification_service import VerificationService
from execution import DockerExecutor, WorkspaceManager


class ExecutionService:
    def __init__(self, config, settings: SettingsService, budget: BudgetService):
        self.config = config
        self.settings = settings
        self.budget = budget
        self.log = LogService()
        self.verification = VerificationService(config, settings, budget)

    def _update_execution_cost(self, execution: TaskExecution) -> None:
        billed_amount = case(
            (AIUsage.cost > 0, AIUsage.cost),
            else_=func.coalesce(AIUsage.allowance, 0),
        )
        total, requests = (
            db.session.query(
                func.coalesce(func.sum(billed_amount), 0),
                func.count(AIUsage.id),
            )
            .filter(AIUsage.task_id == execution.task_id, AIUsage.created_at >= execution.started_at)
            .one()
        )
        execution.ai_cost = float(total or 0)
        execution.ai_requests = int(requests or 0)
        db.session.commit()

    def _executions_today(self) -> int:
        start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return int(
            db.session.query(func.count(func.distinct(TaskExecution.task_id)))
            .filter(TaskExecution.started_at >= start)
            .scalar()
            or 0
        )

    def _check_daily_task_limit(self) -> None:
        limit = self.settings.get_int("agent.max_tasks_per_day", 10)
        if self._executions_today() >= limit:
            raise ExecutionError(f"Daily task execution limit reached ({limit})")

    def claim(self, task: Task, actor: str = "worker") -> dict:
        task = db.session.get(Task, task.id)
        if task.claimed_at is not None:
            return {"status": "already_claimed", "claimed_at": task.claimed_at.isoformat()}
        if task.automation_allowed is not True:
            raise AutomationNotAllowedError("Automation permission has not been verified for this task")
        if task.platform.automation_permission != "allowed":
            raise AutomationNotAllowedError("Platform automation permission is not confirmed")
        adapter = build_adapter(task.platform, self.config, self.settings)
        updated = (
            db.session.query(Task)
            .filter(Task.id == task.id, Task.claimed_at.is_(None))
            .update({"claimed_at": utcnow()}, synchronize_session=False)
        )
        db.session.commit()
        if not updated:
            db.session.refresh(task)
            return {"status": "already_claimed", "claimed_at": task.claimed_at.isoformat() if task.claimed_at else None}
        try:
            result = adapter.claim_task(task)
        except AutomationNotAllowedError as exc:
            task = db.session.get(Task, task.id)
            task.claimed_at = None
            db.session.commit()
            self.log.warning("Claim skipped by adapter", task_id=task.id, error=str(exc))
            return {"status": "claim_skipped", "message": str(exc)}
        except Exception as exc:
            task = db.session.get(Task, task.id)
            task.claimed_at = None
            db.session.commit()
            raise
        if result is not None:
            evaluation = task.evaluation or {}
            evaluation["claim"] = {
                "reference": result.reference,
                "url": result.url,
                "status": result.status,
                "message": result.message,
            }
            task.evaluation = evaluation
            db.session.commit()
            self.log.info("Task claimed", task_id=task.id, reference=result.reference, actor=actor)
            return {"status": result.status, "reference": result.reference, "url": result.url}
        return {"status": "claimed", "reference": None}

    def execute(self, task: Task, actor: str = "worker", manual: bool = False) -> TaskExecution:
        task = db.session.get(Task, task.id)
        if task is None:
            raise TaskStateError("Task not found")
        if task.status not in {TaskStatus.APPROVED, TaskStatus.MANUAL_REVIEW}:
            raise TaskStateError(f"Task cannot be executed from status {task.status}")
        if task.automation_allowed is not True:
            raise AutomationNotAllowedError("Automation permission has not been verified for this task")
        if not self.settings.get_bool("agent.enabled", True):
            raise TaskStateError("Agent is disabled")
        mode = self.settings.get("agent.mode", "observation")
        if mode == "observation":
            raise TaskStateError("Execution is disabled in observation mode")
        if mode == "autonomous" and task.platform.automation_permission != "allowed":
            raise AutomationNotAllowedError(
                "Autonomous execution requires verified platform automation permission"
            )
        if not manual:
            self._check_daily_task_limit()

        attempt = int(
            db.session.query(func.count(TaskExecution.id)).filter(TaskExecution.task_id == task.id).scalar() or 0
        ) + 1
        execution = TaskExecution(
            task_id=task.id,
            attempt=attempt,
            status="running",
            started_at=utcnow(),
        )
        db.session.add(execution)
        db.session.commit()
        transition(task, TaskStatus.EXECUTING, f"execution attempt {attempt} started", actor=actor)

        workspace = None
        execution_report: dict = {"commands": [], "command_results": [], "logs": ""}
        try:
            provider = build_ai_provider(self.config, self.settings, self.budget)
            provider.set_task(task.id)
            self.budget.ensure_can_spend(provider.estimate_execution_cost("execution"))
            context = build_task_context(task)
            response = provider.execute_task(context, (task.evaluation or {}).get("analysis"))
            workspace_manager = WorkspaceManager(
                self.config.resolve_path(self.settings.get("execution.workspace_root", "var/workspaces")),
                self.config.resolve_path(self.settings.get("execution.artifacts_dir", "var/artifacts")),
            )
            workspace = workspace_manager.create(task.id, attempt)
            written = workspace_manager.write_files(workspace, response.data.get("files") or [])
            if not written:
                raise ExecutionError("AI execution produced no files")
            execution.model = response.usage.model
            execution.session_id = provider.session_id
            execution.ai_requests = provider.request_count
            execution.workspace_path = str(workspace)
            execution.output_summary = response.data.get("summary") or response.data.get("notes") or ""
            commands = response.data.get("commands") or []
            execution_report["commands"] = commands
            logs = []

            blocked_reason = None
            if commands:
                docker_enabled = self.settings.get_bool("execution.docker_enabled", True)
                if not docker_enabled:
                    blocked_reason = "Docker execution is disabled; validation commands were not run"
                else:
                    executor = DockerExecutor(
                        image=self.settings.get("execution.docker_image", "jobagents-sandbox:latest"),
                        memory_mb=self.settings.get_int("execution.max_memory_mb", 512),
                        cpus=self.settings.get_float("execution.max_cpus", 0.5),
                        network_enabled=self.settings.get_bool("execution.network_enabled_by_default", False),
                        max_minutes=self.settings.get_int("execution.max_execution_minutes", 15),
                    )
                    for command in commands:
                        self.log.info("Running sandbox command", task_id=task.id, command=command[:200])
                        result = executor.run(workspace, command)
                        execution_report["command_results"].append(
                            {
                                "command": command,
                                "exit_code": result.exit_code,
                                "timed_out": result.timed_out,
                                "stdout": result.stdout[-4000:],
                                "stderr": result.stderr[-4000:],
                            }
                        )
                        logs.append(f"$ {command}\n{result.stdout}\n{result.stderr}")
                        if result.timed_out:
                            blocked_reason = "validation command timed out"
                            break
            execution_report["logs"] = "\n".join(logs)[:20000]

            artifacts = workspace_manager.collect_artifacts(workspace)
            store_dir = workspace_manager.artifacts_dir / f"task-{task.id}-attempt-{attempt}"
            records = build_artifact_records(artifacts, workspace, store_dir)
            execution.artifacts = records
            execution.finished_at = utcnow()
            if blocked_reason:
                execution.status = "blocked"
                execution.error = blocked_reason
                db.session.commit()
                self._update_execution_cost(execution)
                transition(task, TaskStatus.MANUAL_REVIEW, blocked_reason, actor=actor)
                return execution
            execution.status = "succeeded"
            db.session.commit()
            transition(task, TaskStatus.COMPLETED, "execution finished", actor=actor)
            self.verification.verify(task, execution, execution_report)
            self._update_execution_cost(execution)
            return execution
        except (AIBudgetExceeded, AILimitExceeded):
            execution.finished_at = utcnow()
            execution.status = "failed"
            execution.error = "AI budget or request limit reached"
            db.session.commit()
            transition(task, TaskStatus.MANUAL_REVIEW, execution.error, actor=actor)
            raise
        except SandboxUnavailableError as exc:
            execution.finished_at = utcnow()
            execution.status = "blocked"
            execution.error = str(exc)[:2000]
            db.session.commit()
            transition(task, TaskStatus.MANUAL_REVIEW, f"sandbox unavailable: {exc}", actor=actor)
            raise
        except Exception as exc:
            db.session.rollback()
            task = db.session.get(Task, task.id)
            execution = db.session.get(TaskExecution, execution.id)
            if execution:
                execution.finished_at = utcnow()
                execution.status = "failed"
                execution.error = str(exc)[:4000]
            db.session.commit()
            if task and task.status == TaskStatus.EXECUTING:
                transition(task, TaskStatus.MANUAL_REVIEW, f"execution error: {exc}", actor=actor)
            self.log.error("Task execution failed", task_id=task.id if task else None, error=str(exc)[:1000])
            raise
