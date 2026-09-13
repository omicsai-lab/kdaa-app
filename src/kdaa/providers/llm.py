"""Provider-neutral inference seam.

The contract is deliberately plain text in, plain text out. Prompt construction, JSON parsing,
evidence resolution and all domain validation live in the Core, so a provider cannot widen what
the application is willing to believe. Nothing here imports a vendor SDK.
"""
import time
from typing import Any, Protocol
from pydantic import Field
from kdaa.domain import Record
from kdaa.errors import LLMError

class LLMRequest(Record):
    task: str = Field(min_length=1, max_length=40)
    prompt_version: str = Field(min_length=1, max_length=80)
    system: str = Field(default="", max_length=100_000)
    user: str = Field(default="", max_length=2_000_000)
    max_output_tokens: int = Field(default=2000, ge=1, le=64_000)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)

class LLMResponse(Record):
    text: str = ""
    provider: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)
    stop_reason: str = ""
    latency_ms: int = Field(default=0, ge=0)
    simulated: bool = False

class LLMProvider(Protocol):
    name: str
    model: str
    region: str
    simulated: bool
    def generate(self, request: LLMRequest) -> LLMResponse: ...

class StubLLMProvider:
    """Explicit fixture provider for tests. It never manufactures displayed live results: every
    response it returns was written by a test, and `simulated` is always True.

    `responses` may be one body, a list consumed in order, or a mapping keyed by task. A callable
    value receives the request and returns the body, which lets a fixture answer with ids that only
    exist after an earlier stage. An `Exception` value is raised instead of returned, which is how
    provider failures, timeouts and malformed output are exercised without a network.
    """
    name = "stub"
    simulated = True
    region = ""

    def __init__(self, responses: Any, *, model: str = "fixture", latency_ms: int = 1):
        self.model = model
        self.latency_ms = latency_ms
        self.calls: list[LLMRequest] = []
        self._responses = responses

    def _next(self, request: LLMRequest) -> Any:
        if isinstance(self._responses, dict):
            if request.task not in self._responses:
                raise LLMError("llm_provider", f"No fixture is configured for task {request.task}.")
            value = self._responses[request.task]
            return value.pop(0) if isinstance(value, list) else value
        if isinstance(self._responses, list):
            if not self._responses:
                raise LLMError("llm_provider", "The fixture script ran out of responses.")
            return self._responses.pop(0)
        return self._responses

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        value = self._next(request)
        if callable(value) and not isinstance(value, type):
            value = value(request)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, LLMResponse):
            return value
        return LLMResponse(text=str(value), provider=self.name, model=self.model,
                           usage={"inputTokens": 0, "outputTokens": 0}, stop_reason="end_turn",
                           latency_ms=self.latency_ms, simulated=True)

def elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
