class ReconRelateError(Exception):
    """Base exception for ReconRelate."""


class ValidationError(ReconRelateError):
    """Raised when input validation fails."""


class ProviderError(ReconRelateError):
    """Raised when a provider call fails."""


class ProviderTimeoutError(ProviderError):
    """Provider exceeded its configured deadline."""


class ProviderRateLimitError(ProviderError):
    """Provider rejected the call because of a quota or rate limit."""


class ProviderCapacityError(ProviderRateLimitError):
    """A local shared rate or concurrency ceiling rejected the call before network I/O."""

    def __init__(self, message: str, *, retry_after: float = 0.05) -> None:
        super().__init__(message)
        self.retry_after = max(0.01, float(retry_after))


class ProviderAuthError(ProviderError):
    """Provider credentials are missing, invalid, or unauthorized."""


class ProviderMalformedError(ProviderError):
    """Provider returned a response that violates its result contract."""


class ProviderInputError(ProviderError):
    """A provider call was rejected locally because its input violates the adapter contract."""


class ProviderResponseLimitError(ProviderMalformedError):
    """Provider response exceeded a declared byte or item ceiling."""


class ProviderBudgetExceededError(ProviderResponseLimitError):
    """Provider exceeded its declared requests or pages for one attempt."""


class RunBudgetExceededError(ProviderError):
    """A run-level logical-call or billable-unit ceiling rejected a call before execution."""


class ModelBudgetExceededError(ReconRelateError):
    """A run-level model call or token ceiling rejected a call before SDK execution."""


class ModelDuplicateReservationError(ModelBudgetExceededError):
    """An identical model request was already admitted and must not be retried ambiguously."""


class ModelPricingUnavailableError(ModelBudgetExceededError):
    """A cloud request has no current verified pre-call dollar price envelope."""


class ProviderCircuitOpenError(ProviderError):
    """Provider calls are temporarily stopped after repeated failures."""


class StorageError(ReconRelateError):
    """Raised when storage operations fail."""


class SecurityError(ReconRelateError):
    """Raised when a scan target or configuration violates security policy."""
