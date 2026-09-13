"""Resolve model-supplied quotations to exact offsets in stored normalized source text.

The model is never asked for, and never believed about, a character offset. It supplies a document
id and a quotation; this module finds that quotation in the stored text itself. A quotation that
occurs zero times is rejected as unfounded, and one that occurs more than once is rejected as
ambiguous, because an ambiguous locator cannot be a stable citation.

The only tolerated adjustments are ones the source pipeline already applies or that cannot change
which characters are cited: Unicode NFC normalization, and trimming whitespace from the ends of the
supplied quotation. Both are attempted as separate exact searches, never as fuzzy matching.
"""
import unicodedata
from dataclasses import dataclass
from uuid import UUID
from kdaa.domain import EvidenceRejection
from kdaa.parsing import sha256

MAX_QUOTE_CHARS = 600
MIN_QUOTE_CHARS = 12
PREVIEW_CHARS = 200

@dataclass(frozen=True)
class ResolvedQuote:
    document_id: UUID
    start: int
    end: int
    quote: str
    paragraph: int

def _paragraph_of(text: str, start: int) -> int:
    """One-based index of the blank-line-separated block containing `start`, matching the
    locator the reference engine reports."""
    return text.count("\n\n", 0, start) + 1

def _occurrences(text: str, needle: str, limit: int = 2) -> list[int]:
    found, index = [], text.find(needle)
    while index != -1 and len(found) < limit:
        found.append(index)
        index = text.find(needle, index + 1)
    return found

def resolve(raw_quote: str, document_id: UUID | None, texts: dict[UUID, str], *, stage: str,
            candidate_title: str = "") -> tuple[ResolvedQuote | None, EvidenceRejection | None]:
    """Return either a resolved quote or an explicit rejection. Never both, never neither."""
    quote = raw_quote if isinstance(raw_quote, str) else ""
    digest = sha256(quote.encode("utf-8", "replace"))
    preview = quote[:PREVIEW_CHARS]

    def reject(reason: str, detail: str) -> tuple[None, EvidenceRejection]:
        return None, EvidenceRejection(stage=stage, reason=reason, document_id=document_id,
                                       candidate_title=candidate_title[:200], quote_sha256=digest,
                                       quote_preview=preview, detail=detail[:400])

    if not quote.strip():
        return reject("empty", "The model supplied an empty quotation.")
    if len(quote) > MAX_QUOTE_CHARS:
        return reject("too_long", f"Quotation is {len(quote)} characters; the evidence limit is {MAX_QUOTE_CHARS}.")
    if document_id is None:
        return reject("unknown_document", "The model did not name a document this run can cite.")
    if document_id not in texts:
        return reject("unselected_document", "The named document is not among this run's selected sources.")

    text = texts[document_id]
    attempts, seen = [], set()
    for variant in (quote, unicodedata.normalize("NFC", quote)):
        for candidate in (variant, variant.strip()):
            if candidate and candidate not in seen:
                seen.add(candidate)
                attempts.append(candidate)
    for candidate in attempts:
        positions = _occurrences(text, candidate)
        if len(positions) == 1:
            if len(candidate) < MIN_QUOTE_CHARS:
                return reject("too_short", f"Quotation is only {len(candidate)} characters; too short "
                                           "to be a stable locator even though it resolved.")
            start = positions[0]
            return ResolvedQuote(document_id=document_id, start=start, end=start + len(candidate),
                                 quote=candidate, paragraph=_paragraph_of(text, start)), None
        if len(positions) > 1:
            return reject("ambiguous", "The quotation appears more than once in that source, so it "
                                       "does not identify a single location.")
    return reject("not_found", "The quotation is not a literal substring of the stored normalized source.")
