"""Live KDAA pipeline: three bounded model stages over the selected source documents.

Boundaries this engine keeps:

* The model reads the documents. It does not decide what becomes evidence. Every quotation is
  resolved against the stored normalized text by `quotes.resolve`, and an unresolved or ambiguous
  quotation is rejected with a recorded reason rather than repaired.
* There is no fallback to the reference engine. A live run that cannot complete fails.
* Input size, output tokens and the number of model calls are bounded before any call is made.
  Nothing is truncated silently; an oversized input is refused.
* Nothing here writes files or talks to a database. The Core supplies a recorder callback.
"""
import json
import re
from dataclasses import dataclass, field
from uuid import UUID, uuid5
from kdaa.domain import (LIVE_DISCLAIMER, LIVE_ENGINE_VERSION, LIVE_SCHEMA_VERSION, Amplification,
                         ArtifactDraft, Assessment, AssetHypothesis, CandidateType, Evidence,
                         EvidenceRejection, LLMInteraction, ProviderRecord, ResultBundle, Run,
                         SemanticAssessment, SourceDocument, Workspace)
from kdaa.errors import KDAAError, LLMError
from kdaa.parsing import sha256
from kdaa.providers.llm import LLMProvider, LLMRequest
from kdaa.provenance import validate_evidence
from . import prompts
from .quotes import resolve

FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)

@dataclass(frozen=True)
class LiveBounds:
    """Hard limits applied before and during a live run."""
    max_input_chars: int = 120_000
    max_output_tokens: int = 4_000
    max_candidates: int = 8
    max_quotes_per_candidate: int = 3
    max_model_calls: int = 12
    timeout_seconds: int = 60
    total_attempts: int = 3

    def as_config(self) -> dict[str, int]:
        return {"max_input_chars": self.max_input_chars, "max_output_tokens": self.max_output_tokens,
                "max_candidates": self.max_candidates,
                "max_quotes_per_candidate": self.max_quotes_per_candidate,
                "max_model_calls": self.max_model_calls, "timeout_seconds": self.timeout_seconds,
                "total_attempts": self.total_attempts}

@dataclass
class _Budget:
    limit: int
    used: int = 0
    def spend(self, stage: str) -> None:
        if self.used >= self.limit:
            raise LLMError("llm_call_budget",
                           f"This run reached its limit of {self.limit} model calls at stage {stage}.",
                           status_code=502)
        self.used += 1

def parse_json(text: str, stage: str) -> dict:
    """Accept one JSON object, optionally inside a code fence. Anything else is a malformed
    response and fails the run: a half-understood answer is not repaired or guessed at."""
    body = text.strip()
    fenced = FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        value = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise LLMError("llm_malformed_response",
                       f"The model did not return valid JSON at the {stage} stage.") from exc
    if not isinstance(value, dict):
        raise LLMError("llm_malformed_response",
                       f"The model returned {type(value).__name__}, not a JSON object, at the {stage} stage.")
    return value

def _text_list(value, limit: int = 10) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip()[:500] for x in value[:limit] if isinstance(x, (str, int, float)) and str(x).strip()]

def _str(value, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""

class LiveEngine:
    """Provider-agnostic: it only needs something implementing the LLMProvider protocol."""
    engine_version = LIVE_ENGINE_VERSION
    schema_version = LIVE_SCHEMA_VERSION

    def __init__(self, provider: LLMProvider, *, bounds: LiveBounds | None = None, recorder=None):
        self.provider = provider
        self.bounds = bounds or LiveBounds()
        # recorder(stage, payload) -> (asset_key, sha256). Supplied by the Core, which owns I/O.
        self.recorder = recorder

    # ---- model plumbing ---------------------------------------------------------------
    def _call(self, budget: _Budget, stage: str, system: str, user: str,
              interactions: list[LLMInteraction]) -> dict:
        budget.spend(stage)
        request = LLMRequest(task=stage, prompt_version=prompts.PROMPT_VERSIONS[stage], system=system,
                             user=user, max_output_tokens=self.bounds.max_output_tokens)
        response = self.provider.generate(request)
        record = {"stage": stage, "prompt_version": request.prompt_version, "model": response.model,
                  "provider": response.provider, "system": system, "user": user,
                  "response_text": response.text, "stop_reason": response.stop_reason,
                  "usage": response.usage, "simulated": response.simulated}
        key = ""
        if self.recorder is not None:
            key = self.recorder(stage, record)
        interactions.append(LLMInteraction(
            stage=stage, prompt_version=request.prompt_version,
            request_sha256=sha256((system + "\x00" + user).encode("utf-8", "replace")),
            response_sha256=sha256(response.text.encode("utf-8", "replace")),
            record_key=key or f"unrecorded:{stage}", stop_reason=response.stop_reason,
            usage=dict(response.usage), latency_ms=response.latency_ms))
        return parse_json(response.text, stage)

    # ---- stages -----------------------------------------------------------------------
    def _discover(self, run: Run, workspace: Workspace, documents: list[SourceDocument],
                  texts: dict[UUID, str], budget: _Budget, interactions: list[LLMInteraction],
                  rejections: list[EvidenceRejection]):
        blocks = [(str(d.id), d.filename, texts[d.id]) for d in documents]
        payload = self._call(budget, "discovery", prompts.DISCOVERY_SYSTEM,
                             prompts.discovery_user(workspace.goal, workspace.focal_unit, blocks,
                                                    self.bounds.max_candidates,
                                                    self.bounds.max_quotes_per_candidate),
                             interactions)
        raw = payload.get("candidates")
        if not isinstance(raw, list):
            raise LLMError("llm_malformed_response", "The discovery response has no candidates list.")
        evidence: dict[tuple[UUID, int, int], Evidence] = {}
        candidates: list[AssetHypothesis] = []
        for entry in raw[:self.bounds.max_candidates]:
            if not isinstance(entry, dict):
                continue
            title = _str(entry.get("title"), 200)
            claim = _str(entry.get("claim"), 2000)
            kind = _str(entry.get("type"), 40).lower().replace(" ", "_").replace("-", "_")
            if not title or not claim or kind not in set(CandidateType):
                rejections.append(EvidenceRejection(
                    stage="discovery", reason="not_found", candidate_title=title[:200],
                    quote_sha256=sha256(json.dumps(entry, sort_keys=True, default=str).encode()),
                    detail="Candidate dropped: missing title, claim, or a known candidate type."))
                continue
            refs: list[UUID] = []
            for quote_entry in (entry.get("quotes") or [])[:self.bounds.max_quotes_per_candidate]:
                if not isinstance(quote_entry, dict):
                    continue
                document_id = _uuid(quote_entry.get("document_id"))
                resolved, rejection = resolve(quote_entry.get("quote", ""), document_id, texts,
                                              stage="discovery", candidate_title=title)
                if rejection is not None:
                    rejections.append(rejection)
                    continue
                key = (resolved.document_id, resolved.start, resolved.end)
                if key not in evidence:
                    item = Evidence(id=uuid5(run.id, f"{key[0]}:{key[1]}:{key[2]}"),
                                    workspace_id=run.workspace_id, run_id=run.id,
                                    document_id=resolved.document_id, text_sha256=_hash_of(documents, resolved.document_id),
                                    start=resolved.start, end=resolved.end, quote=resolved.quote,
                                    paragraph=resolved.paragraph)
                    evidence[key] = item
                if evidence[key].id not in refs:
                    refs.append(evidence[key].id)
            if not refs:
                rejections.append(EvidenceRejection(
                    stage="discovery", reason="not_found", candidate_title=title[:200],
                    quote_sha256=sha256(title.encode()),
                    detail="Candidate dropped: no quotation resolved to the stored source."))
                continue
            candidates.append(AssetHypothesis(
                id=uuid5(run.id, f"candidate:{len(candidates)}:{title}"), workspace_id=run.workspace_id,
                run_id=run.id, candidate_type=CandidateType(kind), title=title, claim=claim,
                evidence_ids=refs, matched_terms=[], discovery_basis="model_reading"))
        return candidates, list(evidence.values())

    def _assess(self, workspace: Workspace, candidates: list[AssetHypothesis],
                evidence: dict[UUID, Evidence], budget: _Budget,
                interactions: list[LLMInteraction]) -> list[Assessment]:
        listing = "\n\n".join(_candidate_block(c, evidence) for c in candidates)
        payload = self._call(budget, "assessment", prompts.ASSESSMENT_SYSTEM,
                             prompts.assessment_user(workspace.goal, workspace.focal_unit, listing),
                             interactions)
        raw = payload.get("assessments")
        if not isinstance(raw, list):
            raise LLMError("llm_malformed_response", "The assessment response has no assessments list.")
        by_id = {}
        for entry in raw:
            if isinstance(entry, dict):
                identifier = _uuid(entry.get("candidate_id"))
                if identifier is not None:
                    by_id[identifier] = entry
        assessments = []
        for candidate in candidates:
            entry = by_id.get(candidate.id)
            explanation = _str((entry or {}).get("goal_relevance_explanation"), 4000)
            if entry is None or not explanation:
                raise LLMError("llm_incomplete_assessment",
                               "The model did not assess every candidate. Nothing was invented to "
                               "fill the gap and the run was not completed.")
            quotes = [evidence[i].quote for i in candidate.evidence_ids]
            assessments.append(Assessment(
                candidate_id=candidate.id, distinct_excerpt_count=len({sha256(q.encode()) for q in quotes}),
                # Lexical fields stay unset: live mode computes no lexical overlap.
                goal_relevance="not_assessed", overlapping_goal_terms=[],
                explanation=explanation,
                missing_evidence=_text_list((entry or {}).get("missing_evidence")) or
                                 ["The model listed no missing evidence; treat that as unverified, not as completeness."],
                rules_version=self.engine_version,
                semantic=SemanticAssessment(
                    goal_relevance_explanation=explanation,
                    possible_uses=_text_list((entry or {}).get("possible_uses")),
                    limitations=_text_list((entry or {}).get("limitations")))))
        return assessments

    def _amplify(self, workspace: Workspace, candidates: list[AssetHypothesis],
                 evidence: dict[UUID, Evidence], budget: _Budget,
                 interactions: list[LLMInteraction]) -> list[Amplification]:
        actions = []
        generated_by = f"{self.engine_version} · {self.provider.name} · {self.provider.model}"
        for candidate in candidates:
            payload = self._call(budget, "amplification", prompts.AMPLIFICATION_SYSTEM,
                                 prompts.amplification_user(workspace.goal, workspace.focal_unit,
                                                            _candidate_block(candidate, evidence)),
                                 interactions)
            content = _str(payload.get("content"), 20000)
            title = _str(payload.get("title"), 200) or candidate.title
            if not content:
                raise LLMError("llm_malformed_response",
                               "The amplification response contained no drafted artifact.")
            supported = set(candidate.evidence_ids)
            grounded = [i for i in (_uuid(x) for x in (payload.get("grounded_evidence_ids") or []))
                        if i in supported]
            actions.append(Amplification(
                candidate_id=candidate.id, evidence_ids=candidate.evidence_ids,
                proposed_artifact=_str(payload.get("proposed_artifact"), 300) or title,
                next_action=_str(payload.get("next_action"), 1000) or
                            "Review the draft against its cited evidence before using it.",
                rationale="Drafted by a language model from this candidate's verified quotations. "
                          "The draft is proposed content, not a source-supported finding.",
                verification_gate=_str(payload.get("verification_gate"), 1000) or
                                  "A qualified person must verify accuracy, ownership and permissions before use.",
                draft=ArtifactDraft(title=title, content=content,
                                    grounded_evidence_ids=grounded or list(candidate.evidence_ids),
                                    proposed_elements=_text_list(payload.get("proposed_elements"), 20),
                                    generated_by=generated_by)))
        return actions

    # ---- entry point -------------------------------------------------------------------
    def run(self, workspace: Workspace, run: Run, documents: list[SourceDocument],
            texts: dict[UUID, str]) -> ResultBundle:
        total = sum(len(texts[d.id]) for d in documents)
        if total > self.bounds.max_input_chars:
            raise KDAAError("llm_input_too_large",
                            f"Live mode accepts {self.bounds.max_input_chars:,} normalized characters per run; "
                            f"this selection has {total:,}. Select fewer documents. Nothing was truncated.", 413)
        budget = _Budget(limit=self.bounds.max_model_calls)
        interactions: list[LLMInteraction] = []
        rejections: list[EvidenceRejection] = []
        candidates, evidence_items = self._discover(run, workspace, documents, texts, budget,
                                                    interactions, rejections)
        evidence = {e.id: e for e in evidence_items}
        # Re-verify every piece of evidence against the stored source before it can be assessed.
        for item in evidence_items:
            document = next(d for d in documents if d.id == item.document_id)
            validate_evidence(item, document, texts[document.id], run.workspace_id, run.id)
        assessments = self._assess(workspace, candidates, evidence, budget, interactions) if candidates else []
        actions = self._amplify(workspace, candidates, evidence, budget, interactions) if candidates else []
        unique = len({d.text_sha256 for d in documents})
        notes = [
            "Candidates, assessments and drafts were written by a language model reading the selected sources.",
            "Every quotation was resolved to exactly one location in the stored normalized text; "
            "unresolved or ambiguous quotations were rejected and are listed with their reason.",
            "Drafted artifacts are proposed content. Only the cited evidence is source-supported.",
            "No score, ranking or confidence value is produced, and ownership remains unresolved.",
        ]
        if rejections:
            notes.append(f"{len(rejections)} model quotation(s) or candidate(s) were rejected during validation.")
        if not candidates:
            notes.insert(0, "No candidate survived evidence validation. That is not a finding that the "
                            "sources lack value.")
        return ResultBundle(
            schema_version=self.schema_version, engine_version=self.engine_version,
            workspace_id=workspace.id, run_id=run.id, config_sha256=run.config_sha256,
            hypotheses=candidates, evidence=evidence_items, assessments=assessments,
            amplifications=actions, input_document_count=len(documents), unique_text_count=unique,
            duplicate_text_count=len(documents) - unique,
            unique_excerpt_count=len({sha256(e.quote.encode()) for e in evidence_items}),
            notes=notes, disclaimer=LIVE_DISCLAIMER, rejections=rejections,
            provider=ProviderRecord(
                provider=self.provider.name, model=self.provider.model,
                region=getattr(self.provider, "region", ""), simulated=self.provider.simulated,
                prompt_versions=dict(prompts.PROMPT_VERSIONS), usage=_sum_usage(interactions),
                calls=budget.used, interactions=interactions))

def _sum_usage(interactions: list[LLMInteraction]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in interactions:
        for key, value in item.usage.items():
            totals[key] = totals.get(key, 0) + int(value)
    return totals

def _uuid(value) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None

def _hash_of(documents: list[SourceDocument], document_id: UUID) -> str:
    return next(d.text_sha256 for d in documents if d.id == document_id)

def _candidate_block(candidate: AssetHypothesis, evidence: dict[UUID, Evidence]) -> str:
    quotes = "\n".join(f'  - evidence id {i}: "{evidence[i].quote}"' for i in candidate.evidence_ids)
    return (f"candidate_id: {candidate.id}\ntype: {candidate.candidate_type.value}\n"
            f"title: {candidate.title}\nclaim: {candidate.claim}\nverified quotations:\n{quotes}")
