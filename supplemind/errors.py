"""Domain-specific exceptions for SuppleMind."""


class SupplementError(Exception):
    """Base class for domain-specific errors."""


class ValidationError(SupplementError):
    """Raised when user input fails validation."""


class SupplementNotFoundError(SupplementError):
    """Raised when the requested supplement does not exist."""


class InsufficientStockError(SupplementError):
    """Raised when a take operation would make stock negative."""
