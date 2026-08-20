"""Public exception hierarchy for :mod:`docreconstruct`."""


class DocReconstructError(Exception):
    """Base class for recoverable framework errors."""


class UnsupportedInputError(DocReconstructError):
    """Raised when no installed provider can read an input."""


class ProviderUnavailableError(DocReconstructError):
    """Raised when a configured provider cannot run in this environment."""


class RendererUnavailableError(DocReconstructError):
    """Raised when a requested output renderer is unavailable."""


class ReconstructionError(DocReconstructError):
    """Raised when an otherwise valid reconstruction cannot be completed."""


class LayoutBudgetExceededError(DocReconstructError):
    """Raised when a layout source would need more pages or pixels than allowed.

    Page rasters are held in memory while they are analyzed, and a small
    compressed PDF can decode to orders of magnitude more, so an unbounded
    document is an out-of-memory risk rather than a slow one.  This is
    deliberately outside the error types the PDF extractors fall back on: a
    document over budget must be refused, not retried through another backend
    that would decode the same pages again.
    """
