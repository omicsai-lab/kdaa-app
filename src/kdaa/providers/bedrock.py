"""Amazon Bedrock adapter for the Converse API. The only module that imports the AWS SDK.

Verified against the current boto3 `bedrock-runtime.converse` reference: `modelId`, `messages`
(role/content text blocks), `system` blocks, `inferenceConfig.maxTokens`, and a response of
`output.message.content[].text`, `stopReason` and `usage.{inputTokens,outputTokens}`.

Credentials are never read, stored or logged here. The client uses the standard boto3 credential
chain (environment, shared config/credentials file, container or instance role), so whatever the
operator has configured locally is what is used. No credential is ever accepted as a parameter.

Attempts are bounded with botocore's `total_max_attempts`, which counts the initial request plus
retries, so the SDK cannot retry beyond the configured budget.
"""
import json
import time
from typing import Any
from kdaa.errors import LLMError
from .llm import LLMRequest, LLMResponse, elapsed_ms

PROVIDER_NAME = "aws-bedrock-converse"
# Service-side conditions that a bounded SDK retry may legitimately help with.
RETRYABLE = {"ThrottlingException", "ModelTimeoutException", "ServiceUnavailableException",
             "InternalServerException", "ModelNotReadyException", "RequestTimeout",
             "RequestTimeoutException"}
# Conditions a retry cannot fix; surface them with an actionable message instead.
TERMINAL = {
    "AccessDeniedException": "The configured AWS identity is not allowed to invoke this Bedrock model. Grant bedrock:InvokeModel for it, or request model access in the Bedrock console.",
    "ResourceNotFoundException": "Bedrock does not recognize this model or inference profile ID in this region. Check BEDROCK_MODEL_ID and BEDROCK_REGION.",
    "ValidationException": "Bedrock rejected the request as invalid. Check that the model ID is a Converse-capable model or inference profile for this region.",
}

class BedrockConverseProvider:
    """One configurable model through the official Converse API. No streaming, no tools, no
    agents: a single bounded request/response per call."""
    name = PROVIDER_NAME
    simulated = False

    def __init__(self, *, region: str, model_id: str, timeout_seconds: int = 60,
                 total_attempts: int = 3, client: Any = None):
        if not region or not model_id:
            raise LLMError("llm_not_configured",
                           "Live mode needs BEDROCK_REGION and BEDROCK_MODEL_ID. No model is guessed.",
                           status_code=503)
        self.region, self.model = region, model_id
        self.timeout_seconds, self.total_attempts = timeout_seconds, total_attempts
        self._client = client if client is not None else self._build_client()

    def _build_client(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - the dependency is pinned in the image
            raise LLMError("llm_not_configured", "The AWS SDK is not installed in this image.",
                           status_code=503) from exc
        # total_max_attempts counts the initial request, so this is the whole attempt budget.
        config = Config(region_name=self.region, retries={"total_max_attempts": self.total_attempts,
                                                          "mode": "standard"},
                        connect_timeout=min(10, self.timeout_seconds), read_timeout=self.timeout_seconds)
        return boto3.client("bedrock-runtime", config=config)

    def generate(self, request: LLMRequest) -> LLMResponse:
        body = {
            "modelId": self.model,
            "messages": [{"role": "user", "content": [{"text": request.user}]}],
            "inferenceConfig": {"maxTokens": request.max_output_tokens,
                                "temperature": request.temperature},
        }
        if request.system:
            body["system"] = [{"text": request.system}]
        started = time.monotonic()
        try:
            response = self._client.converse(**body)
        except Exception as exc:
            raise self._translate(exc) from exc
        return self._read(response, started)

    def _read(self, response: dict, started: float) -> LLMResponse:
        stop_reason = str(response.get("stopReason", ""))
        blocks = (response.get("output", {}).get("message", {}) or {}).get("content", []) or []
        text = "".join(block["text"] for block in blocks if isinstance(block, dict) and "text" in block)
        if stop_reason == "max_tokens":
            # Refuse a half-written answer rather than silently accepting a truncated one.
            raise LLMError("llm_output_truncated",
                           "The model hit the output-token limit. Raise LLM_MAX_OUTPUT_TOKENS or "
                           "select fewer documents; a truncated answer is not accepted.")
        if stop_reason in {"content_filtered", "guard_intervened"}:
            raise LLMError("llm_content_filtered", "The model or a guardrail blocked this response.")
        if not text.strip():
            raise LLMError("llm_empty_response", "The model returned no text content.")
        usage = response.get("usage", {}) or {}
        return LLMResponse(
            text=text, provider=self.name, model=self.model, stop_reason=stop_reason,
            usage={k: int(v) for k, v in usage.items() if isinstance(v, (int, float))},
            latency_ms=elapsed_ms(started), simulated=False)

    def _translate(self, exc: Exception) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        name = type(exc).__name__
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str(response.get("Error", {}).get("Code", "")) or ""
        label = code or name
        if name in {"NoCredentialsError", "PartialCredentialsError", "NoRegionError"} or label == "UnrecognizedClientException":
            return LLMError("llm_no_credentials",
                            "No usable AWS credentials were found by the standard credential chain. "
                            "Configure a profile or environment credentials for this container.",
                            status_code=503)
        if name in {"ReadTimeoutError", "ConnectTimeoutError", "ConnectionError", "EndpointConnectionError"}:
            return LLMError("llm_timeout",
                            f"Bedrock did not answer within {self.timeout_seconds}s after at most "
                            f"{self.total_attempts} attempt(s).", retryable=True, status_code=504)
        if label in TERMINAL:
            return LLMError("llm_rejected", TERMINAL[label], status_code=502)
        if label in RETRYABLE:
            return LLMError("llm_unavailable",
                            f"Bedrock returned {label} after the configured {self.total_attempts} "
                            "attempt(s). No further retry was made.", retryable=True, status_code=503)
        # Never echo an SDK message: it can contain request content.
        return LLMError("llm_provider", f"The Bedrock request failed ({label}).")

def describe(payload: dict) -> str:
    """Stable serialization used for the recorded request/response hashes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
