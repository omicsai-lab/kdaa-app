"""Build the run engines this process offers, from configuration only.

Reference mode is always available. Live mode appears only when the operator has explicitly
enabled it and named a region and model; otherwise the Bedrock client is never constructed, so an
unconfigured deployment cannot make a paid call by accident. There is no automatic downgrade from
live to reference: an unavailable mode is refused at the API.
"""
import os

from kdaa.core.llm_engine import LiveBounds, LiveEngine
from .config import LIVE_MODE, Settings

# Compose forwards these as empty strings when the operator has not set them, and botocore treats an
# empty AWS_PROFILE as a request for a profile literally named "", failing with ProfileNotFound
# before it ever tries the rest of the credential chain. An empty value means "not configured".
AWS_PASSTHROUGH = ("AWS_PROFILE", "AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                   "AWS_SESSION_TOKEN")

def drop_empty_aws_environment() -> list[str]:
    removed = [name for name in AWS_PASSTHROUGH if os.environ.get(name, "").strip() == ""
               and name in os.environ]
    for name in removed:
        del os.environ[name]
    return removed

def build_engines(settings: Settings) -> dict:
    if not settings.live_available():
        return {}
    drop_empty_aws_environment()
    from kdaa.providers.bedrock import BedrockConverseProvider
    provider = BedrockConverseProvider(
        region=settings.bedrock_region, model_id=settings.bedrock_model_id,
        timeout_seconds=settings.llm_timeout_seconds, total_attempts=settings.llm_total_attempts)
    bounds = LiveBounds(
        max_input_chars=settings.llm_max_input_chars, max_output_tokens=settings.llm_max_output_tokens,
        max_candidates=settings.llm_max_candidates, max_quotes_per_candidate=settings.llm_max_quotes,
        max_model_calls=settings.llm_max_calls, timeout_seconds=settings.llm_timeout_seconds,
        total_attempts=settings.llm_total_attempts)
    return {LIVE_MODE: LiveEngine(provider, bounds=bounds)}
