"""Bedrock adapter behaviour with a fake client. No network, no credentials, no cost.

Passing these does not demonstrate that a real Bedrock model works: they exercise request shaping
and failure translation only.
"""
import pytest
from kdaa.errors import LLMError
from kdaa.providers.bedrock import BedrockConverseProvider
from kdaa.providers.llm import LLMRequest

REQUEST = LLMRequest(task="discovery", prompt_version="v1", system="be careful", user="do the thing",
                     max_output_tokens=1234)

def ok_response(text="{}", stop="end_turn"):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "stopReason": stop, "usage": {"inputTokens": 10, "outputTokens": 4}}

class FakeClient:
    def __init__(self, result):
        self.result, self.calls = result, []
    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

def provider(result, **kwargs):
    return BedrockConverseProvider(region="us-east-1", model_id="us.example.model-v1:0",
                                   client=FakeClient(result), **kwargs)

def test_builds_a_converse_request_in_the_documented_shape():
    p = provider(ok_response('{"candidates": []}'))
    response = p.generate(REQUEST)
    sent = p._client.calls[0]
    assert sent["modelId"] == "us.example.model-v1:0"
    assert sent["messages"] == [{"role": "user", "content": [{"text": "do the thing"}]}]
    assert sent["system"] == [{"text": "be careful"}]
    assert sent["inferenceConfig"]["maxTokens"] == 1234
    assert response.text == '{"candidates": []}'
    assert response.usage == {"inputTokens": 10, "outputTokens": 4}
    assert response.simulated is False and response.stop_reason == "end_turn"

def test_truncated_output_is_refused_rather_than_accepted():
    with pytest.raises(LLMError) as error:
        provider(ok_response("half an ans", stop="max_tokens")).generate(REQUEST)
    assert error.value.code == "llm_output_truncated"

@pytest.mark.parametrize("stop", ["content_filtered", "guard_intervened"])
def test_filtered_output_is_an_explicit_failure(stop):
    with pytest.raises(LLMError) as error:
        provider(ok_response("", stop=stop)).generate(REQUEST)
    assert error.value.code == "llm_content_filtered"

def test_empty_output_is_an_explicit_failure():
    with pytest.raises(LLMError) as error:
        provider(ok_response("   ")).generate(REQUEST)
    assert error.value.code == "llm_empty_response"

class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}

@pytest.mark.parametrize("code,expected,retryable", [
    ("AccessDeniedException", "llm_rejected", False),
    ("ResourceNotFoundException", "llm_rejected", False),
    ("ValidationException", "llm_rejected", False),
    ("ThrottlingException", "llm_unavailable", True),
    ("ModelTimeoutException", "llm_unavailable", True),
    ("ServiceUnavailableException", "llm_unavailable", True),
])
def test_service_errors_are_translated_to_safe_domain_errors(code, expected, retryable):
    with pytest.raises(LLMError) as error:
        provider(ClientError(code)).generate(REQUEST)
    assert error.value.code == expected and error.value.retryable is retryable
    assert "do the thing" not in error.value.message and "be careful" not in error.value.message

class ReadTimeoutError(Exception):
    pass

class NoCredentialsError(Exception):
    pass

def test_timeout_is_reported_with_the_configured_bounds():
    with pytest.raises(LLMError) as error:
        provider(ReadTimeoutError("slow"), timeout_seconds=7, total_attempts=2).generate(REQUEST)
    assert error.value.code == "llm_timeout" and "7s" in error.value.message
    assert "2 attempt" in error.value.message

def test_missing_credentials_is_reported_as_configuration_not_a_bug():
    with pytest.raises(LLMError) as error:
        provider(NoCredentialsError()).generate(REQUEST)
    assert error.value.code == "llm_no_credentials" and error.value.status_code == 503

def test_unknown_sdk_errors_never_echo_the_sdk_message():
    with pytest.raises(LLMError) as error:
        provider(RuntimeError("secret-token=abc123 leaked from the SDK")).generate(REQUEST)
    assert error.value.code == "llm_provider"
    assert "abc123" not in error.value.message and "secret" not in error.value.message

def test_attempt_budget_is_configured_as_total_attempts(monkeypatch):
    """botocore counts total_max_attempts including the first call, so the budget is the whole
    number of requests the SDK may make."""
    captured = {}
    class FakeConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    fake_boto3 = type("M", (), {"client": staticmethod(lambda name, config=None: FakeClient(ok_response()))})
    import sys, types
    botocore_config = types.ModuleType("botocore.config")
    botocore_config.Config = FakeConfig
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.config", botocore_config)
    BedrockConverseProvider(region="eu-west-1", model_id="m", timeout_seconds=11, total_attempts=2)
    assert captured["retries"] == {"total_max_attempts": 2, "mode": "standard"}
    assert captured["read_timeout"] == 11 and captured["region_name"] == "eu-west-1"
