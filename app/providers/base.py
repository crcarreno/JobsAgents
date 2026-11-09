from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskContext:
    task_id: int
    platform: str
    external_id: str
    title: str
    description: str
    url: str
    reward: float
    currency: str
    deadline: str | None = None
    expected_output: str | None = None
    language: str | None = None
    automation_notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "platform": self.platform,
            "external_id": self.external_id,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "reward": self.reward,
            "currency": self.currency,
            "deadline": self.deadline,
            "expected_output": self.expected_output,
            "language": self.language,
            "automation_notes": self.automation_notes,
            "extra": self.extra,
        }


@dataclass
class AIUsageRecord:
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    allowance: float | None
    billing_source: str | None
    duration_ms: int


@dataclass
class AIResponse:
    data: dict[str, Any]
    usage: AIUsageRecord
    raw_text: str


class AIProvider(ABC):
    name = "base"

    @abstractmethod
    def analyze_task(self, context: TaskContext) -> AIResponse:
        raise NotImplementedError

    @abstractmethod
    def evaluate_feasibility(self, context: TaskContext, analysis: dict[str, Any]) -> AIResponse:
        raise NotImplementedError

    @abstractmethod
    def execute_task(self, context: TaskContext, execution_plan: dict[str, Any] | None = None) -> AIResponse:
        raise NotImplementedError

    @abstractmethod
    def verify_result(
        self, context: TaskContext, execution_result: dict[str, Any], artifacts: list[dict[str, Any]]
    ) -> AIResponse:
        raise NotImplementedError

    @abstractmethod
    def estimate_execution_cost(self, request_type: str = "analysis") -> float:
        raise NotImplementedError

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        raise NotImplementedError

    def reset_request_counter(self) -> None:
        pass

    def set_task(self, task_id: int | None) -> None:
        pass
