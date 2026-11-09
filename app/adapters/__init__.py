from app.adapters.base_adapter import (
    BaseAdapter,
    ConnectionResult,
    SubmissionResult,
    TaskCandidate,
    TaskDetail,
)
from app.adapters.github_adapter import GitHubBountyAdapter
from app.adapters.registry import adapter_names, build_adapter

__all__ = [
    "BaseAdapter",
    "ConnectionResult",
    "GitHubBountyAdapter",
    "SubmissionResult",
    "TaskCandidate",
    "TaskDetail",
    "adapter_names",
    "build_adapter",
]
