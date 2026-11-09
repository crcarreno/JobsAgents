import json
from dataclasses import dataclass

from core.database import db
from core.exceptions import (
    AIBudgetExceeded,
    AILimitExceeded,
    AIProviderError,
    AutomationNotAllowedError,
    PlatformError,
)
from core.security import detect_prompt_injection
from app.adapters import build_adapter
from app.adapters.base_adapter import TaskCandidate
from app.models import PlatformConfig, Task, TaskStatus
from app.providers import build_ai_provider
from app.providers.base import TaskContext
from app.services.budget_service import BudgetService
from app.services.log_service import LogService
from app.services.profitability_service import ProfitabilityService
from app.services.settings_service import SettingsService
from app.services.task_service import transition


@dataclass
class EvaluationOutcome:
    task_id: int
    status: str
    reason: str
    net_profit: float


class EvaluatorService:
    def __init__(self, config, settings: SettingsService, budget: BudgetService):
        self.config = config
        self.settings = settings
        self.budget = budget
        self.profitability = ProfitabilityService(settings)
        self.log = LogService()

    def evaluate_task(self, task: Task) -> EvaluationOutcome:
        task = db.session.get(Task, task.id)
        if task is None or task.platform is None or not task.platform.enabled:
            raise PlatformError("Task platform is missing or disabled")
        provider = build_ai_provider(self.config, self.settings, self.budget) if self.settings.get_ai_api_key() else None
        try:
            if task.status != TaskStatus.PENDING:
                transition(task, TaskStatus.EVALUATING, "manual re-evaluation requested", actor="admin")
                return self._evaluate_task(task, provider, mark_evaluating=False)
            return self._evaluate_task(task, provider)
        except Exception as exc:
            db.session.rollback()
            current = db.session.get(Task, task.id)
            if current is not None and current.status == TaskStatus.EVALUATING:
                try:
                    transition(
                        current,
                        TaskStatus.FAILED,
                        f"evaluation error: {exc}",
                        actor="admin",
                    )
                except Exception:
                    db.session.rollback()
            raise

    def evaluate_pending(self, limit: int = 20) -> list[EvaluationOutcome]:
        tasks = (
            Task.query.filter(Task.status == TaskStatus.PENDING)
            .order_by(Task.created_at.asc())
            .limit(limit)
            .all()
        )
        outcomes = []
        provider = None
        api_available = bool(self.settings.get_ai_api_key())
        if api_available:
            provider = build_ai_provider(self.config, self.settings, self.budget)
        for task in tasks:
            if not task.platform or not task.platform.enabled:
                continue
            try:
                outcome = self._evaluate_task(task, provider)
                outcomes.append(outcome)
            except (AIBudgetExceeded, AILimitExceeded) as exc:
                db.session.rollback()
                self.log.warning("AI budget reached; pausing evaluation", error=str(exc))
                break
            except Exception as exc:
                db.session.rollback()
                self.log.error("Task evaluation failed", task_id=task.id, error=str(exc)[:1000])
                task = db.session.get(Task, task.id)
                if task and task.status in {TaskStatus.PENDING, TaskStatus.EVALUATING}:
                    transition(task, TaskStatus.FAILED, f"evaluation error: {exc}", actor="evaluator")
        return outcomes

    def _evaluate_task(self, task: Task, provider, mark_evaluating: bool = True) -> EvaluationOutcome:
        if mark_evaluating:
            transition(task, TaskStatus.EVALUATING, "automatic evaluation started", actor="evaluator")
        adapter = build_adapter(task.platform, self.config, self.settings)
        candidate = TaskCandidate(
            external_id=task.external_id,
            title=task.title,
            url=task.url,
            description=task.description,
            reward=float(task.reward_amount or 0),
            currency=task.currency,
            raw=task.raw_payload or {},
        )
        detail = adapter.get_task_details(candidate)
        automation_status, automation_notes = self._automation_status(adapter, task.platform, candidate, detail)

        context = TaskContext(
            task_id=task.id,
            platform=task.platform.slug,
            external_id=detail.external_id,
            title=detail.title,
            description=detail.description,
            url=detail.url,
            reward=detail.reward,
            currency=detail.currency,
            deadline=detail.deadline.isoformat() if detail.deadline else None,
            expected_output=detail.expected_output,
            language=detail.language,
            automation_notes=automation_notes,
            extra={**detail.extra, "automation_status": automation_status},
        )

        ai_used = False
        analysis: dict = {}
        feasibility: dict = {}
        if provider is not None:
            try:
                provider.set_task(task.id)
                response = provider.analyze_task(context)
                analysis = response.data
                ai_used = True
                if analysis.get("can_execute_with_code") and not analysis.get("prompt_injection_detected"):
                    feasibility = provider.evaluate_feasibility(context, analysis).data
            except (AIBudgetExceeded, AILimitExceeded):
                raise
            except AIProviderError as exc:
                self.log.warning("AI analysis unavailable during evaluation", task_id=task.id, error=str(exc))
        if not analysis:
            analysis = self._heuristic_analysis(detail, task)

        injection_hits = detect_prompt_injection(detail.description)
        if injection_hits:
            analysis["prompt_injection_detected"] = True
            analysis.setdefault("red_flags", []).append("possible prompt injection detected")

        estimated_minutes = int(analysis.get("estimated_minutes") or 60)
        success_probability = float(analysis.get("success_probability") or 0.35)
        complexity = int(analysis.get("complexity") or 3)
        platform_fee_percent = detail.extra.get("platform_fee_percent")

        profitability = self.profitability.calculate(
            reward=detail.reward,
            estimated_minutes=estimated_minutes,
            success_probability=success_probability,
            ai_cost=None if provider is None else provider.estimate_execution_cost("analysis")
            + provider.estimate_execution_cost("execution")
            + provider.estimate_execution_cost("verification"),
            platform_fee_percent=platform_fee_percent,
        )

        task.title = detail.title
        task.description = detail.description
        task.url = detail.url
        task.reward_amount = detail.reward
        task.currency = detail.currency
        task.deadline_at = detail.deadline
        task.estimated_minutes = estimated_minutes
        task.complexity = complexity
        task.success_probability = success_probability
        task.platform_fee = profitability.platform_fee
        task.ai_cost_estimate = profitability.ai_cost
        task.infrastructure_cost = profitability.infrastructure_cost
        task.net_profit = profitability.net_profit
        task.hourly_rate = profitability.hourly_rate
        task.automation_allowed = True if automation_status == "allowed" else (
            False if automation_status == "denied" else None
        )
        task.automation_notes = automation_notes
        task.risk_score = self._risk_score(analysis, complexity, success_probability, automation_status)
        task.raw_payload = {**detail.raw, "repo": detail.extra.get("repo"), "default_branch": detail.extra.get("default_branch")}
        task.category = str(analysis.get("category") or "")[:120] or None
        task.evaluation = {
            "analysis": analysis,
            "feasibility": feasibility,
            "ai_used": ai_used,
            "expected_output": detail.expected_output,
            "language": detail.language,
            "repo": detail.extra.get("repo"),
            "default_branch": detail.extra.get("default_branch"),
            "automation": {"status": automation_status, "notes": automation_notes},
            "profitability": {
                "reward": profitability.reward,
                "platform_fee": profitability.platform_fee,
                "ai_cost": profitability.ai_cost,
                "infrastructure_cost": profitability.infrastructure_cost,
                "net_profit": profitability.net_profit,
                "hourly_rate": profitability.hourly_rate,
                "reasons": profitability.reasons,
            },
        }

        status, reason = self._decide(task, analysis, feasibility, profitability, automation_status, ai_used)
        transition(task, status, reason, actor="evaluator")
        self.log.info(
            "Task evaluated",
            task_id=task.id,
            external_id=task.external_id,
            status=status,
            reason=reason,
            net_profit=profitability.net_profit,
        )
        return EvaluationOutcome(task_id=task.id, status=status, reason=reason, net_profit=float(task.net_profit))

    def _automation_status(self, adapter, platform: PlatformConfig, candidate, detail) -> tuple[str, str]:
        adapter_status, adapter_notes = adapter.check_automation_permission(candidate)
        platform_status = platform.automation_permission or "unknown"
        if platform_status == "denied" or adapter_status == "denied":
            return "denied", f"Platform: {platform.automation_permission_notes or platform_status}. {adapter_notes}"
        if adapter_status == "allowed" and platform_status in {"allowed", "unknown"}:
            return "allowed", adapter_notes
        if adapter_status == "unknown":
            return "unknown", adapter_notes
        return platform_status, platform.automation_permission_notes or adapter_notes

    def _decide(self, task, analysis, feasibility, profitability, automation_status, ai_used):
        if automation_status == "denied":
            return TaskStatus.REJECTED, "automation is not permitted for this task or repository"
        if analysis.get("prompt_injection_detected"):
            return TaskStatus.MANUAL_REVIEW, "possible prompt injection in task content"
        if not analysis.get("can_execute_with_code"):
            return TaskStatus.REJECTED, "task cannot be executed by code within the agent capabilities"
        if not profitability.viable:
            return TaskStatus.REJECTED, "not profitable: " + "; ".join(profitability.reasons)
        if not ai_used:
            return TaskStatus.MANUAL_REVIEW, "AI analysis unavailable; manual review required"
        if not feasibility:
            return TaskStatus.MANUAL_REVIEW, "feasibility analysis unavailable; manual review required"
        if not feasibility.get("feasible"):
            reasons = "; ".join(feasibility.get("blocking_reasons") or []) or "feasibility rejected"
            return TaskStatus.MANUAL_REVIEW, f"feasibility requires review: {reasons}"
        if automation_status != "allowed" and self.settings.get_bool(
            "security.require_automation_permission", True
        ):
            return (
                TaskStatus.MANUAL_REVIEW,
                "automation permission not verified; approve explicitly in the panel before execution",
            )
        if task.risk_score >= 0.8:
            return TaskStatus.MANUAL_REVIEW, "risk score is too high for autonomous execution"
        return TaskStatus.APPROVED, "task is feasible, profitable and permitted"

    @staticmethod
    def _risk_score(analysis, complexity, success_probability, automation_status) -> float:
        score = float(complexity) / 5 * 0.35 + (1 - float(success_probability)) * 0.35
        score += 0.15 if automation_status != "allowed" else 0.0
        if analysis.get("prompt_injection_detected"):
            score += 0.5
        score += min(0.15, len(analysis.get("red_flags") or []) * 0.03)
        return round(min(1.0, max(0.0, score)), 3)

    @staticmethod
    def _heuristic_analysis(detail, task) -> dict:
        description = (detail.description or "").lower()
        estimated = 90 if len(description) > 4000 else 45
        return {
            "category": "unclassified",
            "can_execute_with_code": True,
            "automation_risk": "unknown",
            "requires_network": False,
            "requires_external_account": False,
            "estimated_minutes": estimated,
            "complexity": 3,
            "success_probability": 0.4,
            "required_tools": [],
            "automation_flags": ["ai_analysis_unavailable"],
            "red_flags": [],
            "reasoning": "Heuristic estimate because no AI analysis was available",
            "prompt_injection_detected": False,
        }
