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
