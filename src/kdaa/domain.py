"""Small, versioned public records; IDs and offsets do not depend on any UI."""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SCHEMA_VERSION = "1.0"
# Live results add records that have no lexical equivalent, so they carry their own schema version.
# Version 1.0 bundles remain readable: every field added for 2.0 is optional with a default.
LIVE_SCHEMA_VERSION = "2.0"
ENGINE_VERSION = "reference-rules-v0.1"
LIVE_ENGINE_VERSION = "live-converse-v0.1"
NORMALIZATION_VERSION = "unicode-nfc-lines-v1"
DISCLAIMER = (
    "Local reference PoC, not a reproduction or validation of Paper B. "
    "No live LLM calls. Candidates are provisional; lexical evidence is not "
    "proof of capability, ownership, independence, or realized value."
)
LIVE_DISCLAIMER = (
    "Live language-model output, not a reproduction or validation of Paper B and not a "
    "calibrated assessment. Every quotation was resolved to an exact offset in the stored "
    "normalized source and unresolved or ambiguous quotations were rejected, but an exact "
    "quotation proves source linkage only. Candidates remain provisional, ownership remains "
    "unresolved, and generated draft content is proposed material, not source-supported fact."
)
DRAFT_NOTE = (
    "Generated draft. Only the linked evidence is source-supported; every other statement is "
    "proposed content requiring human verification before use."
)
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class Workspace(Record):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=120)
    focal_unit: str = Field(default="My workspace", min_length=1, max_length=200)
    goal: str = Field(default="", max_length=2000)
    created_at: datetime = Field(default_factory=utcnow)

class SourceDocument(Record):
    id: UUID
    workspace_id: UUID
    filename: str
    media_type: str
    raw_key: str
    text_key: str
    raw_sha256: Sha256
    text_sha256: Sha256
    normalization_version: str = NORMALIZATION_VERSION
    parser_version: str
    byte_count: int = Field(ge=1)
    character_count: int = Field(ge=1)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

class SourceSnapshot(Record):
    document_id: UUID
    filename: str
    raw_sha256: Sha256
    text_sha256: Sha256
    normalization_version: str
    parser_version: str

class Evidence(Record):
    id: UUID
    workspace_id: UUID
    run_id: UUID
    document_id: UUID
    text_sha256: Sha256
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=600)
    paragraph: int = Field(ge=1)
    @model_validator(mode="after")
    def valid_length(self):
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("Evidence offsets must match Unicode code-point quote length.")
        return self

class CandidateType(StrEnum):
    SOFTWARE = "software"
    DATASET = "dataset"
    METHOD = "method"
    TEACHING = "teaching"
    RESEARCH_IDEA = "research_idea"

class AssetHypothesis(Record):
    id: UUID
    workspace_id: UUID
    run_id: UUID
    candidate_type: CandidateType
    title: str
    claim: str
    evidence_ids: list[UUID] = Field(min_length=1)
    matched_terms: list[str] = Field(default_factory=list)
    # Lexical rules and a model reading are different operations; do not read one as the other.
    discovery_basis: Literal["lexical_rules", "model_reading"] = "lexical_rules"
    status: Literal["provisional"] = "provisional"
    ownership: Literal["unresolved"] = "unresolved"

class SemanticAssessment(Record):
    """Model-written assessment. Kept separate from the lexical-overlap fields, which stay
    `not_assessed`/empty in live mode because no lexical overlap was computed. No score is
    produced: a number here would imply a calibration this application does not have."""
    schema_version: Literal["2.0"] = LIVE_SCHEMA_VERSION
    goal_relevance_explanation: str = Field(min_length=1, max_length=4000)
    possible_uses: list[str] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    basis: Literal["model_reading"] = "model_reading"

class Assessment(Record):
    candidate_id: UUID
    traceability: Literal["exact_quotes_verified"] = "exact_quotes_verified"
    distinct_excerpt_count: int = Field(ge=1)
    goal_relevance: Literal["lexical_overlap", "no_overlap", "not_assessed"]
    overlapping_goal_terms: list[str]
    explanation: str
    missing_evidence: list[str]
    rules_version: str = ENGINE_VERSION
    semantic: SemanticAssessment | None = None

class ArtifactDraft(Record):
    """An actual drafted artifact, not instructions for producing one. `content` is proposed
    material; only `grounded_evidence_ids` is backed by verified source quotations."""
    schema_version: Literal["2.0"] = LIVE_SCHEMA_VERSION
    title: str = Field(min_length=1, max_length=200)
    format: Literal["markdown"] = "markdown"
    content: str = Field(min_length=1, max_length=20000)
    grounded_evidence_ids: list[UUID] = Field(default_factory=list)
    proposed_elements: list[str] = Field(default_factory=list, max_length=20)
    provenance_note: str = DRAFT_NOTE
    generated_by: str = Field(min_length=1, max_length=200)

class Amplification(Record):
    candidate_id: UUID
    evidence_ids: list[UUID] = Field(min_length=1)
    proposed_artifact: str
    next_action: str
    rationale: str
    verification_gate: str
    baseline: str = "No reuse baseline supplied; compare usefulness with current practice manually."
    status: Literal["proposal_only"] = "proposal_only"
    draft: ArtifactDraft | None = None

class EvidenceRejection(Record):
    """An explicit, recorded outcome for a model quotation that could not be resolved to exactly
    one place in the stored normalized source. Rejected quotations never become evidence."""
    schema_version: Literal["2.0"] = LIVE_SCHEMA_VERSION
    stage: str = Field(min_length=1, max_length=40)
    reason: Literal["not_found", "ambiguous", "empty", "too_short", "too_long",
                    "unknown_document", "unselected_document"]
    document_id: UUID | None = None
    candidate_title: str = Field(default="", max_length=200)
    quote_sha256: Sha256
    quote_preview: str = Field(default="", max_length=200)
    detail: str = Field(default="", max_length=400)

class LLMInteraction(Record):
    """Metadata for one model call. The request and response bodies are written to the asset
    store under `record_key`, never to Git or to ordinary application logs."""
    schema_version: Literal["2.0"] = LIVE_SCHEMA_VERSION
    stage: str = Field(min_length=1, max_length=40)
    prompt_version: str = Field(min_length=1, max_length=80)
    request_sha256: Sha256
    response_sha256: Sha256
    record_key: str = Field(min_length=1, max_length=300)
    stop_reason: str = Field(default="", max_length=80)
    usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = Field(default=0, ge=0)

class ProviderRecord(Record):
    """What actually produced a live bundle: provider, model, prompt versions and token usage."""
    schema_version: Literal["2.0"] = LIVE_SCHEMA_VERSION
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=300)
    region: str = Field(default="", max_length=40)
    simulated: bool
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)
    calls: int = Field(default=0, ge=0)
    interactions: list[LLMInteraction] = Field(default_factory=list)

class ResultBundle(Record):
    schema_version: str = SCHEMA_VERSION
    engine_version: str = ENGINE_VERSION
    workspace_id: UUID
    run_id: UUID
    config_sha256: Sha256
    hypotheses: list[AssetHypothesis]
    evidence: list[Evidence]
    assessments: list[Assessment]
    amplifications: list[Amplification]
    input_document_count: int
    unique_text_count: int
    duplicate_text_count: int
    unique_excerpt_count: int
    notes: list[str]
    disclaimer: str = DISCLAIMER
    provider: ProviderRecord | None = None
    rejections: list[EvidenceRejection] = Field(default_factory=list)
    @model_validator(mode="after")
    def validate_links(self):
        evidence = {x.id: x for x in self.evidence}
        candidates = {x.id: x for x in self.hypotheses}
        if len(evidence) != len(self.evidence) or len(candidates) != len(self.hypotheses):
            raise ValueError("Duplicate result IDs are not allowed.")
        for item in [*self.evidence, *self.hypotheses]:
            if item.run_id != self.run_id or item.workspace_id != self.workspace_id:
                raise ValueError("Results must belong to this run and workspace.")
        for candidate in self.hypotheses:
            if len(set(candidate.evidence_ids)) != len(candidate.evidence_ids):
                raise ValueError("Candidate evidence references must be unique.")
            if not set(candidate.evidence_ids) <= evidence.keys():
                raise ValueError("An evidence reference is unresolved.")
        if {a.candidate_id for a in self.assessments} != candidates.keys():
            raise ValueError("Each candidate needs an assessment.")
        if len(self.assessments) != len(candidates):
            raise ValueError("Duplicate assessments are not allowed.")
        if {a.candidate_id for a in self.amplifications} != candidates.keys():
            raise ValueError("Each candidate needs an amplification proposal.")
        if len(self.amplifications) != len(candidates):
            raise ValueError("Duplicate amplification proposals are not allowed.")
        for action in self.amplifications:
            supporting = set(candidates[action.candidate_id].evidence_ids)
            if not set(action.evidence_ids) <= supporting:
                raise ValueError("Amplification evidence must support its candidate.")
            if action.draft and not set(action.draft.grounded_evidence_ids) <= supporting:
                raise ValueError("A draft may only cite evidence that supports its candidate.")
        return self

class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

class Run(Record):
    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    status: RunStatus = RunStatus.PENDING
    inputs: list[SourceSnapshot] = Field(min_length=1, max_length=20)
    config: dict[str, Any]
    config_sha256: Sha256
    engine_version: str = ENGINE_VERSION
    schema_version: str = SCHEMA_VERSION
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    result: ResultBundle | None = None
    error: dict[str, str] | None = None
    @model_validator(mode="after")
    def validate_result(self):
        if self.status == RunStatus.SUCCEEDED and self.result is None:
            raise ValueError("A succeeded run needs a result bundle.")
        if self.status != RunStatus.SUCCEEDED and self.result is not None:
            raise ValueError("Only succeeded runs may carry final results.")
        if self.result and (self.result.run_id != self.id or self.result.workspace_id != self.workspace_id
                            or self.result.config_sha256 != self.config_sha256):
            raise ValueError("Result bundle does not match run identity/configuration.")
        if self.result and self.result.engine_version != self.engine_version:
            raise ValueError("Result bundle was produced by a different engine than the run records.")
        if len({i.document_id for i in self.inputs}) != len(self.inputs):
            raise ValueError("Run input IDs must be unique.")
        return self
    def transition(self, status: RunStatus, **changes: Any) -> "Run":
        allowed = {
            RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.INTERRUPTED, RunStatus.FAILED},
            RunStatus.RUNNING: {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.INTERRUPTED},
        }
        if status not in allowed.get(self.status, set()):
            raise ValueError(f"Illegal run transition {self.status} -> {status}.")
        return Run.model_validate({**self.model_dump(), **changes, "status": status, "updated_at": utcnow()})

class ProvenanceEvent(Record):
    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    run_id: UUID | None = None
    actor: Literal["local-user", "system"] = "local-user"
    action: str
    source_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    output_ids: list[UUID] = Field(default_factory=list)
    engine_version: str = ENGINE_VERSION
    config_sha256: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)
