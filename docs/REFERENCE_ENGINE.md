# Reference engine: what its outputs mean

Engine ID: **reference-rules-v0.1**. Intended use: exercise the real application/data/provenance contracts while cloud and live models remain out of scope. This is not a reconstructed or revalidated Paper B experiment.

## Discovery

The engine inspects actual normalized source paragraphs, not filenames or canned answer cards. Centralized rules in `src/kdaa/core/rules.py` detect these English literal terms:

| Candidate type | Triggers |
| --- | --- |
| Software | software, python, package, script, repository |
| Dataset | dataset, data dictionary, cohort, annotations |
| Method | protocol, method, procedure, workflow |
| Teaching | lecture, curriculum, assignment, teaching, tutorial |
| Research idea | hypothesis, research question, research idea, pilot study |

Matching is bounded and deterministic. It produces at most one candidate of each type per unique normalized document and up to three distinct matching paragraphs for a candidate. Evidence quotations are capped at 600 code points. Minimum paragraph size and exact matching behavior are in the rule implementation/config snapshot. The algorithm does not understand negation, synonyms, deep semantics or non-English meaning; text saying a capability is absent can still trigger a candidate. Candidates must remain provisional.

Normalized duplicates retain separate source records and remain in run input snapshots; only the first equivalent normalized text contributes candidate/evidence generation. Equal quotations are reported as distinct excerpts only once in the relevant count. Neither different filenames nor distinct excerpts establish independent corroboration.

## Assessment

The reference assessment reports whether the cited strings exactly match their stored sources, a distinct-excerpt count, literal goal-term overlap where a goal exists, an explanation and missing verification evidence. It does not produce calibrated confidence, a quality/ownership probability, or a value score. Literal overlap is not a semantic relevance model. No goal means `not_assessed`, not an invented neutral score.

Evidence linkage proves only that a quoted span exists in a particular stored text. It does not prove the claim is true or attributable to the focal unit. The UI separates `provisional` status and `unresolved` ownership.

## Amplification

Each discovered type has a transparent proposal template that produces a candidate artifact, next action, rationale, human verification gate and suggested baseline. Proposals reference the supporting evidence. They are not autonomous actions and are not proof of realized value. No external tool is called, file published, collaborator contacted or scientific conclusion established.

## Text and provenance

TXT/MD use UTF-8 (BOM accepted); normalization standardizes newlines, NFC Unicode and selected whitespace. Original bytes are always preserved separately. PDF and DOCX are parsed in bounded subprocesses. PDF page layout is not preserved, OCR is absent, and DOCX headers/comments/revisions/layout are not fully represented.

Evidence records store source ID, normalized-text SHA-256, paragraph number, start/end code-point offsets and the exact quote. The backend returns surrounding context so the browser does not reinterpret Python code points as JavaScript UTF-16 indices. Hash/quote/identifier/scope validation is repeated when evidence is inspected or a run exported.

A run snapshots input hashes, normalization/parser versions and the rule configuration. Binary parser metadata includes the actual installed pypdf or python-docx version, not just the wrapper version. It can be audited without treating a provider's prose as trusted evidence. Repeating equivalent inputs preserves the rule-derived content; generated run/document IDs and timestamps naturally differ.

## Examples to try

The three bundled demo documents are fictional; they yield five candidates from actual software, dataset, method, teaching and research-idea trigger paragraphs. The negative fixture intentionally yields none. Importing the same text again preserves the duplicate source but does not increase the candidate/support count. A filename containing `python` with irrelevant body text does not create software evidence.

Replacing the reference engine with a real model is a separate development/evaluation step. Preserve the truthful UI labels and test gates until a provider genuinely exists.
