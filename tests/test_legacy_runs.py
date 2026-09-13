"""A run stored before live mode existed must still load, export and display unchanged.

The schema-1.0 fixture in `fixtures_legacy_run.json` is a real reference bundle captured from the
previous milestone. Every field added for live results is optional, so this parses without any
migration, and nothing in it is reinterpreted as a semantic or model-produced result.
"""
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from kdaa.domain import LIVE_ENGINE_VERSION, ResultBundle, Run, RunStatus

FIXTURE = json.loads((Path(__file__).parent / "fixtures_legacy_run.json").read_text())

def test_schema_1_0_run_still_parses():
    run = Run.model_validate(FIXTURE)
    assert run.status == RunStatus.SUCCEEDED
    assert run.schema_version == "1.0" and run.engine_version == "reference-rules-v0.1"

def test_legacy_records_are_not_reinterpreted_as_model_output():
    run = Run.model_validate(FIXTURE)
    bundle = run.result
    # Nothing gains a live meaning by default.
    assert bundle.provider is None and bundle.rejections == []
    assert bundle.hypotheses[0].discovery_basis == "lexical_rules"
    assert bundle.assessments[0].semantic is None
    assert bundle.amplifications[0].draft is None
    assert "No live LLM calls" in bundle.disclaimer

def test_legacy_run_round_trips_through_json_unchanged():
    run = Run.model_validate(FIXTURE)
    again = Run.model_validate(json.loads(run.model_dump_json()))
    assert again == run

def test_legacy_run_still_exports(tmp_path):
    from kdaa.core.reports import markdown_report
    report = markdown_report(Run.model_validate(FIXTURE), [])
    assert "Software: A small Python package" in report
    assert "The software pac" in report

def test_a_run_cannot_claim_an_engine_its_bundle_did_not_produce():
    broken = json.loads(json.dumps(FIXTURE))
    broken["engine_version"] = LIVE_ENGINE_VERSION
    with pytest.raises(ValidationError, match="different engine"):
        Run.model_validate(broken)
