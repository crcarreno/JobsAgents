import re
from datetime import datetime
from typing import Any

import httpx

from core.exceptions import (
    AutomationNotAllowedError,
    PlatformAuthError,
    PlatformError,
    PlatformRateLimitError,
)
from app.adapters.base_adapter import (
    BaseAdapter,
    ConnectionResult,
    SubmissionResult,
    TaskCandidate,
    TaskDetail,
)

BUDGET_PATTERN = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Za-z][A-Za-z0-9]{1,15})?")


class OpenTaskAdapter(BaseAdapter):
    adapter_name = "opentask"
    platform_documentation = {
        "platform": "OpenTask.ai",
        "task_types": (
            "Agent marketplace tasks: pitch work, bounties and benchmarks across engineering, research, "
            "design, data and analysis."
        ),
        "countries": "OpenTask accounts are available internationally; payment availability depends on the wallet and network.",
        "registration": "OpenTask account with a published agent profile; an API token is created at https://opentask.ai/account/tokens",
        "authentication": (
            "Scoped OpenTask API token sent as Authorization: Bearer <token>. Minimum read scopes: "
            "profile:read, tasks:read, capabilities:read. OAuth/DPoP also exist for other client types."
        ),
        "apis": (
            "Official REST API at https://opentask.ai/api and OpenAPI at https://opentask.ai/api/openapi. "
            "Public discovery: GET /api/tasks and GET /api/tasks/{taskId}; authenticated profile: GET /api/agent/me; "
            "authenticated detail: GET /api/agent/tasks/{taskId}; seller recommendations: "
            "GET /api/agent/me/task-recommendations."
        ),
        "automation_restrictions": (
            "This first integration is read-only. Bidding, claiming, entries, deliveries and payments are not "
            "implemented. OpenTask permits scoped API tokens for service automation; writing actions require a "
            "separate authorization and are blocked here."
        ),
        "payments": (
            "Wallet-to-wallet payments. Task owners pay a 4.5% platform fee on top of the agreed worker amount; "
            "the worker receives the agreed amount. A declared reward is a commitment, not escrow: "
            "rewardTerms.fundingStatus is currently not_escrowed."
        ),
        "fees": "4.5% platform fee paid by the task owner; network fees may apply.",
        "limits": (
            "Profile and task reads: 120 requests/minute. Seller recommendations: 60 requests/minute. "
            "List pages accept limit up to 100."
        ),
    }

    def __init__(self, platform, config, settings, token: str = ""):
        super().__init__(platform, config, settings, token)
        self.api_base = str(self.option("base_url", "https://opentask.ai/api")).rstrip("/")
        self.web_base = str(self.option("public_base_url", "https://opentask.ai")).rstrip("/")
        self.max_results = max(1, int(self.option("max_results", 30)))
        self.timeout_seconds = float(self.option("timeout_seconds", 45))
        self.catalog_source = str(self.option("catalog_source", "public")).lower()
        self.include_weak = bool(self.option("include_weak", False))
        self.user_agent = str(self.option("user_agent", self.settings.get("ai.user_agent", "jobagents-agent/1.0")))

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _error_info(response: httpx.Response) -> tuple[str, str]:
        try:
            payload = response.json()
        except ValueError:
            return "", (response.text or "")[:200]
        code = str(payload.get("code") or "")[:100]
        message = str(payload.get("error") or "")[:300]
        return code, message

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout_seconds)) as client:
                response = client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.TimeoutException as exc:
            raise PlatformError("OpenTask request timed out") from exc
        except httpx.HTTPError as exc:
            raise PlatformError(f"OpenTask network error ({type(exc).__name__})") from exc

        if response.status_code == 401:
            raise PlatformAuthError(
                "OpenTask token is missing, invalid, expired, or revoked"
            )
        if response.status_code == 403:
            code, message = self._error_info(response)
            detail = code or message or "insufficient scope or policy"
            raise PlatformAuthError(f"OpenTask authorization failed: {detail}")
        if response.status_code == 404:
            raise PlatformError("OpenTask resource not found")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if not retry_after:
                try:
                    retry_after = response.json().get("retryAfterSeconds")
                except ValueError:
                    retry_after = None
            suffix = f" (retry after {retry_after}s)" if retry_after else ""
            raise PlatformRateLimitError(f"OpenTask rate limit exceeded{suffix}")
        if response.status_code >= 400:
            code, message = self._error_info(response)
            detail = code or message or "unknown error"
            raise PlatformError(f"OpenTask API error {response.status_code}: {detail}")
        return response

    def connect(self) -> ConnectionResult:
        if not self.token:
            self._connection = ConnectionResult(
                ok=False,
                status="not_configured",
                message="OPENTASK_API_TOKEN is not configured",
            )
            return self._connection
        try:
            payload = self._request("GET", "/agent/me").json()
            profile = payload.get("profile") or {}
            handle = profile.get("handle") or profile.get("id") or "unknown"
            self._connection = ConnectionResult(
                ok=True,
                status="ok",
                message=f"Connected as {handle}",
                details={
                    "profile_id": profile.get("id"),
                    "handle": handle,
                    "display_name": profile.get("displayName"),
                    "kind": profile.get("kind"),
                    "service_listing_status": profile.get("serviceListingStatus"),
                    "stats": payload.get("stats") or {},
                    "active_payout_method_count": payload.get("activePayoutMethodCount"),
                },
            )
        except PlatformError as exc:
            self._connection = ConnectionResult(ok=False, status="error", message=str(exc))
        return self._connection

    def list_capabilities(self, limit: int = 1) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", "/agent/me/capabilities", params={"limit": max(1, min(100, int(limit)))}
        ).json()
        return payload.get("capabilities") or []

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    @staticmethod
    def _budget(task: dict[str, Any]) -> tuple[float, str]:
        terms = task.get("rewardTerms") or {}
        amount: float | None = None
        currency = terms.get("rewardToken") or task.get("budgetCurrency")

        atomic = terms.get("rewardAmountAtomic")
        decimals = terms.get("rewardTokenDecimals")
        if atomic is not None and decimals is not None:
            try:
                amount = int(str(atomic)) / (10 ** int(decimals))
            except (ValueError, ZeroDivisionError):
                amount = None

        if amount is None and task.get("budgetAmount") is not None:
            try:
                amount = float(str(task["budgetAmount"]).replace(",", ""))
            except ValueError:
                amount = None

        if amount is None:
            match = BUDGET_PATTERN.search(str(task.get("budgetText") or ""))
            if match:
                try:
                    amount = float(match.group(1).replace(",", ""))
                except ValueError:
                    amount = None
                if not currency and match.group(2):
                    currency = match.group(2)

        return (amount or 0.0, str(currency or "USD"))

    def _candidate_from_task(
        self, task: dict[str, Any], recommendation: dict[str, Any] | None = None
    ) -> TaskCandidate:
        task_id = str(task.get("id") or "")
        amount, currency = self._budget(task)
        raw: dict[str, Any] = {"task": task, "source": self.catalog_source}
        if recommendation is not None:
            raw["recommendation"] = recommendation
        return TaskCandidate(
            external_id=task_id,
            title=str(task.get("title") or "")[:500],
            url=f"{self.web_base}/tasks/{task_id}",
            description=str(task.get("description") or ""),
            reward=amount,
            currency=currency,
            created_at=self._parse_dt(task.get("createdAt")),
            raw=raw,
        )

    def _search_public(self, since: datetime | None) -> list[TaskCandidate]:
        params: dict[str, Any] = {"limit": min(100, self.max_results)}
        for key in ("query", "skill", "sort"):
            value = self.option(key)
            if value:
                params[key] = value
        payload = self._request("GET", "/tasks", params=params).json()
        candidates: dict[str, TaskCandidate] = {}
        for item in payload.get("tasks") or []:
            candidate = self._candidate_from_task(item)
            if since and candidate.created_at and candidate.created_at < since:
                continue
            if candidate.external_id:
                candidates.setdefault(candidate.external_id, candidate)
        return list(candidates.values())[: self.max_results]

    def _search_recommendations(self, since: datetime | None) -> list[TaskCandidate]:
        params: dict[str, Any] = {"limit": min(50, self.max_results)}
        if self.include_weak:
            params["includeWeak"] = "1"
        payload = self._request("GET", "/agent/me/task-recommendations", params=params).json()
        candidates: dict[str, TaskCandidate] = {}
        for item in payload.get("recommendations") or []:
            task = item.get("task") or {}
            candidate = self._candidate_from_task(task, recommendation=item)
            if since and candidate.created_at and candidate.created_at < since:
                continue
            if candidate.external_id:
                candidates.setdefault(candidate.external_id, candidate)
        return list(candidates.values())[: self.max_results]

    def search_tasks(self, since: datetime | None = None) -> list[TaskCandidate]:
        if self.catalog_source == "recommendations":
            return self._search_recommendations(since)
        return self._search_public(since)

    def get_task_details(self, candidate: TaskCandidate) -> TaskDetail:
        task_id = candidate.external_id
        path = f"/agent/tasks/{task_id}" if self.token else f"/tasks/{task_id}"
        payload = self._request("GET", path).json()
        task = payload.get("task") or {}
        if not task:
            raise PlatformError("OpenTask returned no task detail")
        amount, currency = self._budget(task)
        deliverable_types = [str(item) for item in (task.get("expectedDeliverableTypes") or [])]
        acceptance = task.get("acceptanceCriteria")
        reward_terms = task.get("rewardTerms") or {}
        extra = {
            "execution_mode": task.get("executionMode"),
            "delivery_structure": task.get("deliveryStructure"),
            "execution_phase": task.get("executionPhase"),
            "status": task.get("status"),
            "visibility": task.get("visibility"),
            "moderation_status": task.get("moderationStatus"),
            "budget_amount": task.get("budgetAmount"),
            "budget_currency": task.get("budgetCurrency"),
            "budget_text": task.get("budgetText"),
            "reward_terms": reward_terms,
            "funding_status": reward_terms.get("fundingStatus"),
            "acceptance_criteria": acceptance,
            "expected_deliverable_types": deliverable_types,
            "capability_requirements": task.get("capabilityRequirements") or [],
            "attachments": task.get("attachments") or [],
            "review_profile": task.get("reviewProfile"),
            "counts": task.get("counts") or task.get("_count") or {},
            "available_actions": payload.get("availableActions") or task.get("availableActions") or [],
            "skills_tags": task.get("skillsTags") or [],
            "owner": task.get("owner") or {},
            "preferred_token": task.get("preferredToken"),
            "payment_network": task.get("paymentNetwork"),
            "entry_quota": payload.get("entryQuota"),
        }
        return TaskDetail(
            external_id=task_id,
            title=str(task.get("title") or candidate.title)[:500],
            url=f"{self.web_base}/tasks/{task_id}",
            description=str(task.get("description") or "")[:20000],
            reward=amount,
            currency=currency,
            created_at=self._parse_dt(task.get("createdAt")) or candidate.created_at,
            expected_output=", ".join(deliverable_types) or (str(acceptance)[:1000] if acceptance else None),
            language=None,
            deadline=self._parse_dt(task.get("deadline")),
            extra=extra,
            raw=payload,
        )

    def check_automation_permission(self, candidate: TaskCandidate) -> tuple[str, str]:
        return (
            "unknown",
            "OpenTask integration is read-only in this phase. Bidding, claiming, entries and delivery are not "
            "implemented and require a separate authorization.",
        )

    def claim_task(self, task) -> SubmissionResult | None:
        raise AutomationNotAllowedError(
            "OpenTask integration is read-only: claim_task is disabled in this phase"
        )

    def submit_task(self, task, artifacts: list[dict[str, Any]], summary: str = "") -> SubmissionResult:
        raise AutomationNotAllowedError(
            "OpenTask integration is read-only: submit_task is disabled in this phase"
        )

    def get_payment_status(self, task) -> dict[str, Any]:
        return {
            "status": "unverifiable",
            "message": (
                "OpenTask payments are wallet-to-wallet and require contract/payment flows that are not enabled "
                "in this read-only phase. A declared reward is a commitment, not escrow "
                "(rewardTerms.fundingStatus is not_escrowed)."
            ),
        }
