import os
from dataclasses import dataclass, field
from pathlib import Path

REFERENCE_MODE = "reference"
LIVE_MODE = "live"
MODES = (REFERENCE_MODE, LIVE_MODE)

def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default

@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "postgresql+psycopg://kdaa:kdaa_local_only@localhost:5432/kdaa_app_local"))
    asset_dir: Path = field(default_factory=lambda: Path(os.getenv("ASSET_DIR", "data/runtime")))
    demo_dir: Path = field(default_factory=lambda: Path(os.getenv("DEMO_DIR", "data/demo")))
    engine_mode: str = field(default_factory=lambda: os.getenv("ENGINE_MODE", REFERENCE_MODE))
    # Live inference is opt-in. Without this flag the Bedrock provider is never constructed and
    # no paid call can be made, whatever else is configured.
    llm_enabled: bool = field(default_factory=lambda: _flag("KDAA_LLM_ENABLED"))
    # No model is guessed: both of these must be supplied explicitly to enable live mode.
    bedrock_region: str = field(default_factory=lambda: os.getenv("BEDROCK_REGION", "").strip())
    bedrock_model_id: str = field(default_factory=lambda: os.getenv("BEDROCK_MODEL_ID", "").strip())
    llm_max_input_chars: int = field(default_factory=lambda: _int("LLM_MAX_INPUT_CHARS", 120_000))
    llm_max_output_tokens: int = field(default_factory=lambda: _int("LLM_MAX_OUTPUT_TOKENS", 4_000))
    llm_max_candidates: int = field(default_factory=lambda: _int("LLM_MAX_CANDIDATES", 8))
    llm_max_quotes: int = field(default_factory=lambda: _int("LLM_MAX_QUOTES_PER_CANDIDATE", 3))
    llm_max_calls: int = field(default_factory=lambda: _int("LLM_MAX_MODEL_CALLS", 12))
    llm_timeout_seconds: int = field(default_factory=lambda: _int("LLM_TIMEOUT_SECONDS", 60))
    llm_total_attempts: int = field(default_factory=lambda: _int("LLM_TOTAL_ATTEMPTS", 3))
    local_origins: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip() for x in os.getenv(
        "LOCAL_ORIGINS", "http://localhost:5183,http://127.0.0.1:5183,http://localhost:8183,http://127.0.0.1:8183").split(",") if x.strip()))

    def validate(self) -> None:
        if self.engine_mode not in MODES:
            raise ValueError(f"Only ENGINE_MODE={' or '.join(MODES)} is implemented. There is no silent fallback.")
        if self.engine_mode == LIVE_MODE and not self.llm_enabled:
            raise ValueError("ENGINE_MODE=live also needs KDAA_LLM_ENABLED=1. Live inference is opt-in.")
        if self.llm_enabled and not (self.bedrock_region and self.bedrock_model_id):
            raise ValueError("KDAA_LLM_ENABLED=1 requires BEDROCK_REGION and BEDROCK_MODEL_ID. "
                             "No region or model is guessed.")
        if self.llm_enabled and self.llm_total_attempts < 1:
            raise ValueError("LLM_TOTAL_ATTEMPTS counts the first call and must be at least 1.")
        if self.llm_enabled and self.llm_timeout_seconds < 1:
            raise ValueError("LLM_TIMEOUT_SECONDS must be at least 1.")
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg://.")

    def live_available(self) -> bool:
        return self.llm_enabled and bool(self.bedrock_region and self.bedrock_model_id)
