from core.database import db
from core.security import contains_secret, detect_prompt_injection
from core.timeutil import utcnow
from app.models import Task, TaskExecution, TaskStatus
from app.providers import build_ai_provider
from app.services.artifact_service import load_artifact_contents
from app.services.log_service import LogService
from app.services.settings_service import SettingsService
from app.services.task_context import build_task_context
from app.services.task_service import transition


class VerificationService:
    def __init__(self, config, settings: SettingsService, budget):
        self.config = config
        self.settings = settings
        self.budget = budget
        self.log = LogService()

    def verify(self, task: Task, execution: TaskExecution, execution_report: dict) -> tuple[str, list[str]]:
        artifacts = load_artifact_contents(execution.artifacts)
        issues: list[str] = []
        if not artifacts:
            issues.append("execution produced no text artifacts")
        for artifact in artifacts:
            content = artifact.get("content") or ""
            if contains_secret(content):
                issues.append(f"possible secret detected in {artifact.get('path')}")
            if detect_prompt_injection(content):
                issues.append(f"suspicious instructions detected in {artifact.get('path')}")
        if execution_report.get("timed_out"):
            issues.append("validation command timed out")
        failed_commands = [
            result
            for result in execution_report.get("command_results", [])
            if result.get("exit_code") not in (0, None) and not result.get("skipped")
        ]
        if failed_commands:
            issues.append(f"{len(failed_commands)} validation command(s) failed")

        ai_verification = None
        api_key = self.settings.get_ai_api_key()
        if api_key and artifacts:
            try:
                provider = build_ai_provider(self.config, self.settings, self.budget)
                provider.set_task(task.id)
                context = build_task_context(task)
                response = provider.verify_result(context, execution_report, artifacts)
                ai_verification = response.data
                if not response.data.get("meets_requirements"):
                    issues.extend(response.data.get("issues") or ["AI verification failed"])
                if response.data.get("sensitive_content_detected"):
                    issues.append("AI verification detected sensitive content")
                if response.data.get("recommendation") == "reject":
                    issues.append("AI verification recommends rejection")
            except Exception as exc:
                self.log.warning("AI verification unavailable", task_id=task.id, error=str(exc)[:500])
                ai_verification = {"error": str(exc)[:500]}

        evaluation = task.evaluation or {}
        evaluation["verification"] = {
            "issues": issues,
            "ai": ai_verification,
            "artifacts": [{"path": a.get("path"), "size": a.get("size")} for a in artifacts],
            "checked_at": utcnow().isoformat(),
        }
        task.evaluation = evaluation
        db.session.commit()

        require_verification = self.settings.get_bool("security.require_task_verification", True)
        if issues and require_verification:
            transition(task, TaskStatus.MANUAL_REVIEW, "verification issues: " + "; ".join(issues[:5]), actor="verifier")
            return TaskStatus.MANUAL_REVIEW, issues
        if issues:
            self.log.warning("Verification produced warnings", task_id=task.id, issues=issues)
        transition(task, TaskStatus.READY_TO_SUBMIT, "verification passed", actor="verifier")
        return TaskStatus.READY_TO_SUBMIT, issues
