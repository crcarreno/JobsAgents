class TaskStatus:
    PENDING = "pending"
    EVALUATING = "evaluating"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    READY_TO_SUBMIT = "ready_to_submit"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"
    MANUAL_REVIEW = "manual_review"

    ALL = [
        PENDING,
        EVALUATING,
        APPROVED,
        EXECUTING,
        COMPLETED,
        READY_TO_SUBMIT,
        SUBMITTED,
        ACCEPTED,
        REJECTED,
        FAILED,
        EXPIRED,
        MANUAL_REVIEW,
    ]

    TERMINAL = {ACCEPTED, REJECTED, FAILED, EXPIRED}


class AutomationPermission:
    UNKNOWN = "unknown"
    ALLOWED = "allowed"
    DENIED = "denied"

    ALL = [UNKNOWN, ALLOWED, DENIED]


class WorkerStatus:
    STOPPED = "stopped"
    RUNNING = "running"
    SCANNING = "scanning"
    PROCESSING = "processing"
    ERROR = "error"


class ExecutionStatus:
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"


class EarningStatus:
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    VOID = "void"


class PlatformConnectionStatus:
    UNKNOWN = "unknown"
    OK = "ok"
    ERROR = "error"
    NOT_CONFIGURED = "not_configured"
