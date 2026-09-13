import io
from pathlib import Path
from alembic import command
from alembic.config import Config
from kdaa.providers.llm import LLMRequest, StubLLMProvider
from kdaa.providers.postgres import metadata
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from kdaa_api.config import Settings
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_llm_seam_is_simulated_explicitly():
    response = StubLLMProvider('{"candidates": []}').generate(
        LLMRequest(task="discovery", prompt_version="test", user="hello"))
    assert response.simulated and response.provider == "stub" and response.model == "fixture"

def test_unimplemented_engine_mode_fails():
    with pytest.raises(ValueError, match="Only ENGINE_MODE"):
        Settings(engine_mode="bedrock").validate()

def test_live_mode_requires_explicit_enablement():
    with pytest.raises(ValueError, match="KDAA_LLM_ENABLED"):
        Settings(engine_mode="live", llm_enabled=False).validate()

def test_enabling_live_requires_an_explicit_region_and_model():
    with pytest.raises(ValueError, match="BEDROCK_REGION and BEDROCK_MODEL_ID"):
        Settings(llm_enabled=True, bedrock_region="", bedrock_model_id="").validate()
    with pytest.raises(ValueError, match="BEDROCK_REGION and BEDROCK_MODEL_ID"):
        Settings(llm_enabled=True, bedrock_region="us-east-1", bedrock_model_id="").validate()
    # A fully specified live configuration is accepted; nothing is guessed on the operator's behalf.
    Settings(llm_enabled=True, bedrock_region="us-east-1",
             bedrock_model_id="us.anthropic.example-model-v1:0").validate()

def test_bedrock_provider_refuses_to_guess_a_model():
    from kdaa.errors import LLMError
    from kdaa.providers.bedrock import BedrockConverseProvider
    with pytest.raises(LLMError, match="No model is guessed"):
        BedrockConverseProvider(region="us-east-1", model_id="")

def test_postgres_ddl_compiles_offline():
    ddl = "\n".join(str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in metadata.sorted_tables)
    assert "JSONB" in ddl and "FOREIGN KEY(workspace_id, run_id)" in ddl
    assert "uq_runs_workspace_id" in ddl

def test_migration_sql_generation(monkeypatch):
    monkeypatch.delenv("KDAA_TEST_SCHEMA", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    output = io.StringIO()
    cfg = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE workspaces" in sql and "CREATE TABLE provenance_events" in sql
    assert "0001_reference" in sql

def test_empty_aws_environment_values_are_treated_as_unset(monkeypatch):
    """Compose forwards unset AWS_* variables as empty strings. An empty AWS_PROFILE must not be
    read as a profile named "", which fails with ProfileNotFound before the credential chain runs."""
    import os
    from kdaa_api.engines import drop_empty_aws_environment
    monkeypatch.setenv("AWS_PROFILE", "")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "   ")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    removed = drop_empty_aws_environment()
    assert "AWS_PROFILE" in removed and "AWS_SESSION_TOKEN" in removed
    assert "AWS_PROFILE" not in os.environ and "AWS_SESSION_TOKEN" not in os.environ
    # A real value is left exactly as the operator set it.
    assert os.environ["AWS_REGION"] == "us-east-1"
