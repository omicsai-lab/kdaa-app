class KDAAError(Exception):
    """A safe, user-facing domain error; never include raw document text."""
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


class NotFound(KDAAError):
    def __init__(self, resource: str):
        super().__init__("not_found", f"{resource} was not found.", 404)


class LLMError(KDAAError):
    """An inference-provider failure. The message is safe to show and never carries credentials,
    prompts or document text. `retryable` describes the class of failure for provenance; it does
    not authorize an unbounded retry loop."""
    def __init__(self, code: str, message: str, *, retryable: bool = False, status_code: int = 502):
        super().__init__(code, message, status_code)
        self.retryable = retryable
