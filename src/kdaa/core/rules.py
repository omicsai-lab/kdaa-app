"""Inspectable reference rules; NOT the Paper B assessment implementation."""
import re
from dataclasses import dataclass
from uuid import UUID, uuid5
from kdaa.domain import (ENGINE_VERSION, Amplification, Assessment, AssetHypothesis, CandidateType,
                         Evidence, ResultBundle, Run, SourceDocument, Workspace)
from kdaa.parsing import sha256
from kdaa.provenance import validate_evidence

@dataclass(frozen=True)
class Rule:
    kind: CandidateType
    label: str
    terms: tuple[str, ...]
    artifact: str
    next_action: str
    gate: str

RULES = (
    Rule(CandidateType.SOFTWARE, "Software", ("software", "python", "script", "package", "repository"),
         "A runnable software demonstration", "Identify an executable entry point; document inputs and run it on a non-sensitive example.",
         "Confirm ownership, dependencies, license, and a successful independent test before sharing."),
    Rule(CandidateType.DATASET, "Dataset", ("dataset", "data dictionary", "cohort", "annotations"),
         "A data-reuse assessment", "Inventory variables, provenance, access conditions, and one concrete reuse question.",
         "Verify consent/access rights, data quality, and task suitability before any reuse."),
    Rule(CandidateType.METHOD, "Method", ("protocol", "method", "procedure", "workflow"),
         "A reusable method checklist", "Turn the described procedure into explicit inputs, steps, outputs, and a small test case.",
         "Have a qualified person verify correctness, scope, and transferability; a mention alone proves none of these."),
    Rule(CandidateType.TEACHING, "Teaching", ("lecture", "curriculum", "assignment", "teaching", "tutorial"),
         "A teaching exercise", "Choose a stated learning objective and draft one exercise using the cited material.",
         "Check source permissions, factual accuracy, and suitability for the intended learners."),
    Rule(CandidateType.RESEARCH_IDEA, "Research idea", ("hypothesis", "research question", "research idea", "pilot study"),
         "A research planning note", "Write a bounded question, the missing evidence, and a feasible first test.",
         "Review novelty, feasibility, ethics, and available evidence before treating the idea as a supported plan."),
)
STOP_WORDS = set("the and for with this that from into their about have has our your are was were can could would should its not but use using reuse a an to of in on as is it be by or at".split())

def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower()) if t not in STOP_WORDS}

def paragraphs(text: str):
    for index, match in enumerate(re.finditer(r"\S[^\n]*(?:\n(?!\n)[^\n]*)*", text), 1):
        yield index, match.start(), match.group()

def title_from_text(text: str) -> str:
    for line in text.splitlines():
        value = line.strip(" #\t")
        if len(value) >= 5:
            return value[:85].rstrip()
    return "Untitled source"

def discover(run: Run, documents: list[SourceDocument], texts: dict[UUID, str]):
    evidence: list[Evidence] = []
    candidates: list[AssetHypothesis] = []
    seen_documents: set[str] = set()
    for document in documents:
        # Earliest selected import is canonical. Every import still remains in the snapshot.
        if document.text_sha256 in seen_documents:
            continue
        seen_documents.add(document.text_sha256)
        text = texts[document.id]
        for rule in RULES:
            refs: list[UUID] = []
            matched_terms: set[str] = set()
            seen_paragraphs: set[str] = set()
            for paragraph, start, block in paragraphs(text):
                if len(block) < 25:
                    continue
                pattern = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in rule.terms) + r")\b", re.I)
                matches = list(pattern.finditer(block))
                if not matches:
                    continue
                digest = sha256(block.encode())
                if digest in seen_paragraphs:
                    continue
                seen_paragraphs.add(digest)
                matched_terms.update(m.group().lower() for m in matches)
                local_start = max(0, matches[0].start() - 120)
                quote = block[local_start:local_start + 600]
                offset = start + local_start
                eid = uuid5(run.id, f"{document.id}:{offset}:{len(quote)}")
                if not any(e.id == eid for e in evidence):
                    item = Evidence(id=eid, workspace_id=run.workspace_id, run_id=run.id,
                        document_id=document.id, text_sha256=document.text_sha256, start=offset,
                        end=offset + len(quote), quote=quote, paragraph=paragraph)
                    validate_evidence(item, document, text, run.workspace_id, run.id)
                    evidence.append(item)
                refs.append(eid)
                if len(refs) >= 3:
                    break
            if refs:
                candidates.append(AssetHypothesis(id=uuid5(run.id, f"{document.id}:{rule.kind}"),
                    workspace_id=run.workspace_id, run_id=run.id, candidate_type=rule.kind,
                    title=f"{rule.label}: {title_from_text(text)}",
                    claim=f"This source contains explicit {rule.label.lower()} references that may support a reusable knowledge-asset hypothesis. Capability and ownership remain unverified.",
                    evidence_ids=refs, matched_terms=sorted(matched_terms)))
    return candidates, evidence

def assess(workspace: Workspace, candidates: list[AssetHypothesis], evidence: list[Evidence]):
    quotes = {item.id: item.quote for item in evidence}
    goal_terms = tokens(workspace.goal)
    assessments = []
    for candidate in candidates:
        support = [quotes[id] for id in candidate.evidence_ids]
        overlap = sorted(goal_terms & tokens(" ".join(support)))
        relevance = "not_assessed" if not goal_terms else "lexical_overlap" if overlap else "no_overlap"
        assessments.append(Assessment(candidate_id=candidate.id,
            distinct_excerpt_count=len({sha256(q.encode()) for q in support}),
            goal_relevance=relevance, overlapping_goal_terms=overlap,
            explanation="Quotations resolve exactly to normalized source text. Distinct excerpts are not independent corroboration; goal overlap is lexical, not a quality or usefulness score.",
            missing_evidence=["Capability scope and present-day validity have not been verified.",
                              "Ownership and reuse permissions remain unresolved.",
                              "Transferability and usefulness for the proposed task need human review."]))
    return assessments

def amplify(candidates: list[AssetHypothesis]):
    rule_by_type = {r.kind: r for r in RULES}
    return [Amplification(candidate_id=c.id, evidence_ids=c.evidence_ids,
            proposed_artifact=rule_by_type[c.candidate_type].artifact,
            next_action=rule_by_type[c.candidate_type].next_action,
            rationale=f"A provisional {c.candidate_type.value.replace('_', ' ')} candidate is linked to exact source excerpts. This type-specific action is a template, not an LLM-generated finding or a promised benefit.",
            verification_gate=rule_by_type[c.candidate_type].gate) for c in candidates]

class ReferenceEngine:
    engine_version = ENGINE_VERSION
    def run(self, workspace: Workspace, run: Run, documents: list[SourceDocument], texts: dict[UUID, str]) -> ResultBundle:
        candidates, evidence = discover(run, documents, texts)
        assessments = assess(workspace, candidates, evidence)
        actions = amplify(candidates)
        unique = len({d.text_sha256 for d in documents})
        notes = ["Exact normalized-document duplicates are retained in the input snapshot but analyzed once.",
                 "English literal trigger rules; negation, hypothetical language, and semantic equivalence are not resolved.",
                 "At most three distinct matching paragraphs per source/type are cited; later matches are not exhaustive."]
        if not candidates:
            notes.insert(0, "No supported candidates under this rule set. This does not establish that the sources have no value.")
        return ResultBundle(workspace_id=workspace.id, run_id=run.id, config_sha256=run.config_sha256,
            hypotheses=candidates, evidence=evidence, assessments=assessments, amplifications=actions,
            input_document_count=len(documents), unique_text_count=unique,
            duplicate_text_count=len(documents)-unique,
            unique_excerpt_count=len({sha256(e.quote.encode()) for e in evidence}), notes=notes)
