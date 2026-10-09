"""Recoverable structured-response failures, distinct from network/provider errors."""


class StructuredOutputError(ValueError):
    """A provider response was empty, truncated, or failed the requested schema."""
