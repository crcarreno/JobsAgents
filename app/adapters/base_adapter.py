from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.exceptions import PlatformError


@dataclass
class TaskCandidate:
    external_id: str
    title: str
    url: str
    description: str = ""
    reward: float = 0.0
    currency: str = "USD"
    created_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskDetail(TaskCandidate):
    expected_output: str | None = None
    language: str | None = None
    deadline: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmissionResult:
    reference: str
    url: str
    status: str
    message: str = ""


@dataclass
class ConnectionResult:
    ok: bool
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class BaseAdapter(ABC):
    adapter_name = "base"
    platform_documentation: dict[str, Any] = {}

    def __init__(self, platform, config, settings, token: str = ""):
        self.platform = platform
        self.config = config
        self.settings = settings
        self.token = token
        self.platform_config = platform.config or {}
        self._connection: ConnectionResult | None = None

    def option(self, key: str, default=None):
        return self.platform_config.get(key, default)

    def connect(self) -> ConnectionResult:
        raise PlatformError("connect() is not supported by this adapter")

    def search_tasks(self, since: datetime | None = None) -> list[TaskCandidate]:
        raise PlatformError("search_tasks() is not supported by this adapter")

    def get_task_details(self, candidate: TaskCandidate) -> TaskDetail:
        raise PlatformError("get_task_details() is not supported by this adapter")

    def check_automation_permission(self, candidate: TaskCandidate) -> tuple[str, str]:
        return "unknown", "Automation permission has not been verified for this platform"

    def claim_task(self, task) -> SubmissionResult | None:
        raise PlatformError("claim_task() is not supported by this adapter")

    def submit_task(self, task, artifacts: list[dict[str, Any]], summary: str = "") -> SubmissionResult:
        raise PlatformError("submit_task() is not supported by this adapter")

    def check_task_status(self, task) -> dict[str, Any]:
        return {"status": "unknown", "message": "Status check is not supported by this adapter"}

    def get_payment_status(self, task) -> dict[str, Any]:
        return {
            "status": "unverifiable",
            "message": "Payment status cannot be verified through this adapter",
        }

    def documentation(self) -> dict[str, Any]:
        return self.platform_documentation
