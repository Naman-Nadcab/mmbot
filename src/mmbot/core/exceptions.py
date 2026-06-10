class PlatformError(Exception):
    """Base exception for platform errors."""


class ConfigurationError(PlatformError):
    """Raised when runtime configuration is invalid."""


class DependencyUnavailableError(PlatformError):
    """Raised when a required dependency cannot be reached."""


class ExchangeError(PlatformError):
    """Raised for exchange transport or protocol errors."""


class RateLimitExceededError(ExchangeError):
    """Raised when a local or remote rate limit is exceeded."""


class RiskViolationError(PlatformError):
    """Raised when risk policy rejects an operation."""


class KillSwitchActiveError(RiskViolationError):
    """Raised when global or scoped kill switch is active."""
