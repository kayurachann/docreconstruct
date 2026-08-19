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
