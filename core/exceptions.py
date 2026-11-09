class JobAgentsError(Exception):
    code = "jobagents_error"

    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context


class ConfigurationError(JobAgentsError):
    code = "configuration_error"


class PlatformError(JobAgentsError):
    code = "platform_error"


class PlatformAuthError(PlatformError):
    code = "platform_auth_error"


class PlatformRateLimitError(PlatformError):
    code = "platform_rate_limit_error"


class AutomationNotAllowedError(PlatformError):
    code = "automation_not_allowed"


class AIProviderError(JobAgentsError):
    code = "ai_provider_error"


class AIBudgetExceeded(AIProviderError):
    code = "ai_budget_exceeded"


class AILimitExceeded(AIProviderError):
    code = "ai_limit_exceeded"


class ExecutionError(JobAgentsError):
    code = "execution_error"


class SandboxUnavailableError(ExecutionError):
    code = "sandbox_unavailable"


class VerificationError(JobAgentsError):
    code = "verification_error"


class DeliveryError(JobAgentsError):
    code = "delivery_error"


class TaskStateError(JobAgentsError):
    code = "task_state_error"
