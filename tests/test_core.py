import json
from uuid import uuid4
import pytest
from pydantic import ValidationError
from kdaa.core.service import KDAAService
from kdaa.domain import Run, RunStatus, Workspace, Evidence, ResultBundle
from kdaa.errors import KDAAError
from kdaa.provenance import validate_evidence

SOFTWARE = b"A small Python package\n\nThe software package contains a script that cleans example field notes. The license and authorship still need checking."
TEACHING = b"A learning plan\n\nThe lecture and assignment explain how learners can link a claim to an exact quotation in teaching material."
UNRELATED = b"A blue sky stretched above the silent forest. A bird sat on a fence until it flew toward the distant hills."

def run_text(core, text=SOFTWARE, goal="Use a Python package for field notes"):
    workspace = core.create_workspace("Example", "Fictional group", goal)
    document = core.import_document(workspace.id, "input.txt", text)
    return workspace, document, core.start_run(workspace.id)

def test_core_callable_without_http(core):
    w, doc, run = run_text(core)
    assert run.status == RunStatus.SUCCEEDED
    assert run.result.hypotheses[0].candidate_type.value == "software"
    assert run.result.hypotheses[0].ownership == "unresolved"
    assert all(h.status == "provisional" for h in run.result.hypotheses)
    assert run.result.assessments[0].goal_relevance == "lexical_overlap"
    assert "package" in run.result.assessments[0].overlapping_goal_terms
    assert core.get_run(run.id) == run

def test_inputs_change_outputs(core):
    _, _, first = run_text(core, SOFTWARE)
    _, _, second = run_text(core, TEACHING)
    assert {h.candidate_type.value for h in first.result.hypotheses} == {"software"}
    assert {h.candidate_type.value for h in second.result.hypotheses} == {"teaching"}
    assert first.result.amplifications[0].proposed_artifact != second.result.amplifications[0].proposed_artifact

def test_unrelated_content_has_no_canned_candidates(core):
    _, _, run = run_text(core, UNRELATED)
    assert run.status == RunStatus.SUCCEEDED
    assert run.result.hypotheses == run.result.assessments == run.result.amplifications == []
    assert "No supported candidates" in run.result.notes[0]

def test_filename_does_not_control_analysis(core):
    w = core.create_workspace("Plain content")
    core.import_document(w.id, "python-software-dataset-lecture.txt", UNRELATED)
    assert not core.start_run(w.id).result.hypotheses

def test_exact_document_duplicates_are_retained_not_counted(core):
    w, first_doc, first = run_text(core)
    duplicate = core.import_document(w.id, "copy.md", SOFTWARE)
    second = core.start_run(w.id)
    assert len(second.inputs) == 2
    assert second.result.unique_text_count == 1
    assert second.result.duplicate_text_count == 1
    assert len(second.result.hypotheses) == len(first.result.hypotheses)
    assert second.result.unique_excerpt_count == first.result.unique_excerpt_count
    assert all(e.document_id == first_doc.id for e in second.result.evidence)
    assert any(duplicate.id in e.source_ids and e.action == "document.imported" for e in core.get_provenance(second.id))

def test_duplicate_paragraph_not_extra_support(core):
    paragraph = b"The software package contains a Python script with example inputs and expected outputs."
    _, _, run = run_text(core, paragraph + b"\n\n" + paragraph)
    assert run.result.assessments[0].distinct_excerpt_count == 1

def test_source_snapshot_is_immutable(core):
    w, doc, original = run_text(core)
    core.import_document(w.id, "more.txt", TEACHING)
    assert len(core.get_run(original.id).inputs) == 1
    assert len(core.start_run(w.id).inputs) == 2
    assert core.get_run(original.id) == original

def test_selected_subset_and_invalid_ids(core):
    w, doc, _ = run_text(core)
    second = core.import_document(w.id, "teaching.txt", TEACHING)
    run = core.start_run(w.id, [second.id])
    assert run.inputs[0].document_id == second.id
    assert {h.candidate_type.value for h in run.result.hypotheses} == {"teaching"}
    with pytest.raises(KDAAError, match="each document"):
        core.start_run(w.id, [doc.id, doc.id])
    with pytest.raises(KDAAError, match="belong"):
        core.start_run(w.id, [uuid4()])
    other = core.create_workspace("Other")
    with pytest.raises(KDAAError, match="belong"):
        core.start_run(other.id, [doc.id])

def test_empty_selection_and_workspace_rejected(core):
    w = core.create_workspace("Empty")
    with pytest.raises(KDAAError, match="between 1 and 20"):
        core.start_run(w.id)
    core.import_document(w.id, "some.txt", SOFTWARE)
    with pytest.raises(KDAAError, match="between 1 and 20"):
        core.start_run(w.id, [])
    with pytest.raises(KDAAError):
        core.create_workspace("   ")

def test_exact_quote_code_point_offsets_and_unicode(core):
    text = "🪴 A sample note\n\nThis Python software package processes café notes and contains a repeatable script example. 中文。"
    w, doc, run = run_text(core, text.encode())
    normalized = core.read_document(doc.id)["text"]
    for evidence in run.result.evidence:
        assert normalized[evidence.start:evidence.end] == evidence.quote
        view = core.inspect_evidence(run.id, evidence.id)
        assert view["quote"] == evidence.quote and view["verified"]
    assert "Unicode code points" in view["locator_unit"]

def test_wrong_quote_and_cross_workspace_rejected(core):
    w, doc, run = run_text(core)
    e = run.result.evidence[0]
    content = core.read_document(doc.id)["text"]
    with pytest.raises(KDAAError):
        validate_evidence(e.model_copy(update={"quote": "X" * len(e.quote)}), doc, content, w.id, run.id)
    with pytest.raises(KDAAError):
        validate_evidence(e.model_copy(update={"workspace_id": uuid4()}), doc, content, w.id, run.id)
    with pytest.raises(KDAAError):
        core.inspect_evidence(uuid4(), e.id)

def test_source_tampering_causes_failed_run(core):
    w, doc, original = run_text(core)
    path = core.assets._path(doc.text_key)
    path.write_text("changed")
    run = core.start_run(w.id)
    assert run.status == RunStatus.FAILED
    assert run.error["code"] == "source_integrity"
    assert run.result is None
    with pytest.raises(KDAAError):
        core.inspect_evidence(original.id, original.result.evidence[0].id)

def test_pipeline_failure_is_persisted(core):
    class Broken:
        def run(self, *args): raise RuntimeError("Synthetic failure")
    core.engines["reference"] = Broken()
    w, doc, run = run_text(core)
    assert run.status == RunStatus.FAILED and run.result is None
    assert core.get_run(run.id).error["stage"] == "reference_pipeline"
    assert "run.failed" in [e.action for e in core.get_provenance(run.id)]

def test_result_and_provenance_commit_atomically(core, store):
    store.fail_action = "run.succeeded"
    w, doc, run = run_text(core)
    assert run.status == RunStatus.FAILED
    assert core.get_run(run.id).result is None
    actions = [e.action for e in core.get_provenance(run.id)]
    assert "run.failed" in actions and "discovery.completed" not in actions

def test_import_rollback_cleans_files(core, store):
    w = core.create_workspace("Rollback")
    store.fail_action = "document.imported"
    with pytest.raises(RuntimeError):
        core.import_document(w.id, "input.txt", SOFTWARE)
    assert not core.list_documents(w.id)
    assert not list(core.assets.root.rglob("*.raw"))
    assert not list(core.assets.root.rglob("*.txt"))

def test_recovery_marks_abandoned_runs_interrupted(core, store):
    w, doc, successful = run_text(core)
    pending = Run(workspace_id=w.id, inputs=successful.inputs, config=successful.config, config_sha256=successful.config_sha256)
    with store.transaction() as tx:
        tx.add_run(pending)
    assert core.recover_interrupted() == 1
    assert core.get_run(pending.id).status == RunStatus.INTERRUPTED
    assert core.get_run(successful.id).status == RunStatus.SUCCEEDED
    assert core.recover_interrupted() == 0

def test_busy_lock_is_explicit_not_background_job(core):
    w = core.create_workspace("Busy")
    core._run_lock.acquire()
    try:
        with pytest.raises(KDAAError, match="Another run is active"):
            core.start_run(w.id)
    finally:
        core._run_lock.release()

def test_invalid_final_run_transition(core):
    _, _, run = run_text(core)
    with pytest.raises(ValueError): run.transition(RunStatus.RUNNING)
    with pytest.raises(ValidationError):
        Run.model_validate({**run.model_dump(), "result": None})

def test_result_bundle_link_validation(core):
    _, _, run = run_text(core)
    payload = run.result.model_dump()
    payload["hypotheses"][0]["evidence_ids"] = [uuid4()]
    with pytest.raises(ValidationError): ResultBundle.model_validate(payload)

def test_exports_contain_verifiable_sources_and_history(core):
    _, doc, run = run_text(core)
    content, media = core.export_run(run.id, "json")
    data = json.loads(content)
    assert media == "application/json"
    assert data["sources"][0]["normalized_text"] == core.read_document(doc.id)["text"]
    assert not {"raw_key", "text_key"} & data["sources"][0].keys()
    assert data["provenance"][-1]["action"] == "run.exported"
    md, media = core.export_run(run.id, "markdown")
    assert "not a reproduction" in md and run.result.evidence[0].quote in md
    assert "Human" not in run.result.hypotheses[0].ownership
    with pytest.raises(KDAAError): core.export_run(run.id, "pdf")

def test_no_goal_does_not_fabricate_relevance(core):
    _, _, run = run_text(core, goal="")
    assert all(a.goal_relevance == "not_assessed" for a in run.result.assessments)

def test_duplicate_quotation_across_distinct_sources_not_independent(core):
    w = core.create_workspace("Overlap")
    para = "The Python package includes a script that handles toy field notes with explicit example inputs."
    core.import_document(w.id, "one.md", ("First source\n\n"+para).encode())
    core.import_document(w.id, "two.md", ("Second source\n\n"+para).encode())
    run = core.start_run(w.id)
    assert run.result.unique_text_count == 2
    assert run.result.unique_excerpt_count == 1
    assert "independent" in run.result.assessments[0].explanation
