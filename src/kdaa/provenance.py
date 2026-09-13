from uuid import UUID
from kdaa.domain import Evidence, ResultBundle, SourceDocument, SourceSnapshot
from kdaa.errors import KDAAError
from kdaa.parsing import sha256

def validate_evidence(evidence: Evidence, document: SourceDocument, text: str,
                      workspace_id: UUID, run_id: UUID) -> None:
    if (evidence.workspace_id != workspace_id or document.workspace_id != workspace_id
            or evidence.run_id != run_id or evidence.document_id != document.id):
        raise KDAAError("invalid_evidence", "Evidence has a cross-workspace/run or unresolved source reference.", 409)
    if sha256(text.encode()) != document.text_sha256 or evidence.text_sha256 != document.text_sha256:
        raise KDAAError("source_integrity", "Stored source text does not match its recorded hash.", 409)
    if text[evidence.start:evidence.end] != evidence.quote:
        raise KDAAError("invalid_evidence", "An evidence quotation does not match the stored source.", 409)

def validate_bundle(bundle: ResultBundle, documents: dict[UUID, SourceDocument],
                    texts: dict[UUID, str], inputs: list[SourceSnapshot]) -> None:
    selected = {s.document_id: s for s in inputs}
    if set(selected) != set(documents) or set(texts) != set(documents):
        raise KDAAError("invalid_snapshot", "Run sources do not match the input snapshot.", 409)
    for doc in documents.values():
        snapshot = selected[doc.id]
        if (doc.workspace_id != bundle.workspace_id or doc.text_sha256 != snapshot.text_sha256
                or doc.raw_sha256 != snapshot.raw_sha256 or doc.parser_version != snapshot.parser_version
                or doc.normalization_version != snapshot.normalization_version):
            raise KDAAError("invalid_snapshot", "Run source metadata no longer matches its snapshot.", 409)
        if sha256(texts[doc.id].encode()) != doc.text_sha256:
            raise KDAAError("source_integrity", "A normalized source hash failed validation.", 409)
    for evidence in bundle.evidence:
        doc = documents.get(evidence.document_id)
        if not doc:
            raise KDAAError("invalid_evidence", "Evidence references a document outside the input snapshot.", 409)
        validate_evidence(evidence, doc, texts[doc.id], bundle.workspace_id, bundle.run_id)
