"""Domain errors raised by the durable CoPenguin runtime."""


class RuntimeInvariantError(RuntimeError):
    """Base class for violations of runtime invariants."""


class ConcurrencyConflict(RuntimeInvariantError):
    """The caller attempted to write from a stale aggregate revision."""


class IdempotencyConflict(RuntimeInvariantError):
    """An idempotency key or event id was reused with different content."""


class InvalidTransition(RuntimeInvariantError):
    """A state transition is not allowed by the deterministic reducer."""


class NotFound(RuntimeInvariantError):
    """A requested runtime aggregate does not exist."""


class ResourceConflict(RuntimeInvariantError):
    """A resource is already held with an incompatible access mode."""


class StaleLease(RuntimeInvariantError):
    """A worker or resource lease is expired or has an obsolete fencing token."""


class ArtifactNotFound(RuntimeInvariantError):
    """An immutable artifact does not exist in the configured CAS."""


class ArtifactCorruption(RuntimeInvariantError):
    """Artifact bytes no longer match their content-addressed identity."""


class ReconciliationRequired(RuntimeInvariantError):
    """An external action may have happened and must not be blindly retried."""


class ExecutionError(RuntimeError):
    """A classified, bounded Executor failure."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        normalized_code = code.strip()
        normalized_message = message.strip()
        if not normalized_code or not normalized_message:
            raise ValueError("execution error code and message are required")
        super().__init__(normalized_message)
        self.code = normalized_code
        self.retryable = retryable


class RetryableExecutionError(ExecutionError):
    """A pure or idempotent Executor operation may be attempted again."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, retryable=True)


class PermanentExecutionError(ExecutionError):
    """The same frozen execution inputs cannot succeed without a new Run."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, retryable=False)
