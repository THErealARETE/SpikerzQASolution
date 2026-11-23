"""Provider error types (own module so low-level code can import them without cycles)."""


class TransientProviderError(RuntimeError):
    """Timeouts, 429s, 5xx: safe to retry."""


class PermanentProviderError(RuntimeError):
    """Bad config, missing credential, 4xx that will not fix itself."""
