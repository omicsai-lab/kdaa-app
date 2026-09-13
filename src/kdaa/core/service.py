"""One application boundary for HTTP, future mobile/MCP/CLI, and all asset I/O."""
import json
import logging
import threading
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4
from kdaa import __version__
from kdaa.domain import (ENGINE_VERSION, NORMALIZATION_VERSION, ProvenanceEvent, Run, RunStatus,
                         SourceDocument, SourceSnapshot, Workspace)
from kdaa.errors import KDAAError, NotFound
from kdaa.parsing import parse_document, sha256
from kdaa.providers.ports import AssetStore, MetadataStore
from kdaa.provenance import validate_bundle, validate_evidence
from .prompts import PROMPT_VERSIONS
from .rules import RULES, ReferenceEngine

logger = logging.getLogger(__name__)
MAX_RUN_TEXT = 250_000
REFERENCE_MODE = "reference"
LIVE_MODE = "live"

class KDAAService:
    """`engines` maps a run mode to its engine. Reference mode is always present and is never
    removed by adding a live one: existing saved runs and new reference runs keep working, and a
    failing live run never falls back to it."""

    def __init__(self, metadata: MetadataStore, assets: AssetStore, *, engine=None,
                 engines: dict | None = None, demo_path: Path | None = None,
                 default_mode: str = REFERENCE_MODE):
        self.metadata, self.assets = metadata, assets
        # One lookup only. A second attribute holding "the" engine would silently diverge from
        # this mapping the moment a caller replaced it.
        self.engines = {REFERENCE_MODE: engine or ReferenceEngine(), **(engines or {})}
        self.demo_path = demo_path
        if default_mode not in self.engines:
            raise ValueError(f"Default run mode {default_mode!r} has no configured engine.")
        self.default_mode = default_mode
        self._run_lock = threading.Lock()
    def available_modes(self) -> list[str]:
        return sorted(self.engines)
    def _engine_for(self, mode: str):
        if mode not in self.engines:
            raise KDAAError("mode_unavailable",
                            f"Run mode {mode!r} is not configured on this server. "
                            f"Available: {', '.join(self.available_modes())}.", 409)
        return self.engines[mode]
    def _record_interaction(self, run_id: UUID, counter: list[int]):
        """Write model request/response bodies to the asset volume, not to the database or logs.
        Only the key and hashes are stored in the result and in provenance."""
        def record(stage: str, payload: dict) -> str:
            counter[0] += 1
            key = f"llm/{run_id}/{counter[0]:02d}-{stage}.json"
            self.assets.put(key, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
            return key
        return record
    def ready(self) -> bool:
        return self.metadata.ready() and self.assets.ready()
    def create_workspace(self, name: str, focal_unit: str = "My workspace", goal: str = "") -> Workspace:
        if not name.strip() or not focal_unit.strip():
            raise KDAAError("invalid_workspace", "Workspace name and focal unit cannot be blank.")
        item = Workspace(name=name.strip(), focal_unit=focal_unit.strip(), goal=goal.strip())
        with self.metadata.transaction() as tx:
            tx.add_workspace(item)
            tx.add_event(ProvenanceEvent(workspace_id=item.id, action="workspace.created", output_ids=[item.id]))
        return item
    def list_workspaces(self) -> list[Workspace]:
        with self.metadata.transaction() as tx:
            return tx.list_workspaces()
    def get_workspace(self, id: UUID) -> Workspace:
        with self.metadata.transaction() as tx:
            item = tx.get_workspace(id)
        if item is None:
            raise NotFound("Workspace")
        return item
    def import_document(self, workspace_id: UUID, filename: str, content: bytes) -> SourceDocument:
        self.get_workspace(workspace_id)
        parsed = parse_document(filename, content)
        id = uuid4()
        raw_key, text_key = f"{workspace_id}/{id}.raw", f"{workspace_id}/{id}.txt"
        item = SourceDocument(id=id, workspace_id=workspace_id, filename=filename,
            media_type=parsed.media_type, raw_key=raw_key, text_key=text_key,
            raw_sha256=sha256(content), text_sha256=sha256(parsed.text.encode()),
            parser_version=parsed.parser_version, byte_count=len(content), character_count=len(parsed.text),
            warnings=parsed.warnings)
        attempted = []
        try:
            for key, data in [(raw_key, content), (text_key, parsed.text.encode())]:
                attempted.append(key)
                self.assets.put(key, data)
            with self.metadata.transaction() as tx:
                tx.add_document(item)
                tx.add_event(ProvenanceEvent(workspace_id=workspace_id, action="document.imported",
                    source_ids=[id], output_ids=[id], details={"raw_sha256": item.raw_sha256,
                    "text_sha256": item.text_sha256, "normalization_version": NORMALIZATION_VERSION,
                    "parser_version": item.parser_version, "bytes": len(content)}))
        except Exception:
            for key in attempted:
                try:
                    self.assets.delete(key)
                except Exception:
                    logger.error("Upload cleanup failed for generated key %s; no domain success recorded.", key)
            raise
        return item
    def list_documents(self, workspace_id: UUID) -> list[SourceDocument]:
        self.get_workspace(workspace_id)
        with self.metadata.transaction() as tx:
            return tx.list_documents(workspace_id)
    def _document(self, id: UUID) -> SourceDocument:
        with self.metadata.transaction() as tx:
            item = tx.get_document(id)
        if item is None:
            raise NotFound("Document")
        return item
    def _text(self, document: SourceDocument) -> str:
        raw = self.assets.read(document.text_key)
        if sha256(raw) != document.text_sha256:
            raise KDAAError("source_integrity", "Normalized source content failed its hash check.", 409)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KDAAError("source_integrity", "Stored normalized text is not valid UTF-8.", 409) from exc
    def read_document(self, id: UUID) -> dict:
        document = self._document(id)
        content = self._text(document)
        with self.metadata.transaction() as tx:
            tx.add_event(ProvenanceEvent(workspace_id=document.workspace_id, action="document.read",
                source_ids=[id], details={"representation": "normalized_text"}))
        return {"document_id": id, "filename": document.filename, "text": content,
                "text_sha256": document.text_sha256, "normalization_version": document.normalization_version}
    def read_original(self, id: UUID) -> tuple[SourceDocument, bytes]:
        document = self._document(id)
        content = self.assets.read(document.raw_key)
        if sha256(content) != document.raw_sha256:
            raise KDAAError("source_integrity", "Original content failed its hash check.", 409)
        with self.metadata.transaction() as tx:
            tx.add_event(ProvenanceEvent(workspace_id=document.workspace_id, action="document.read",
                source_ids=[id], details={"representation": "original_bytes"}))
        return document, content
    def get_run(self, id: UUID) -> Run:
        with self.metadata.transaction() as tx:
            run = tx.get_run(id)
        if run is None:
            raise NotFound("Run")
        return run
    def list_runs(self, workspace_id: UUID) -> list[Run]:
        self.get_workspace(workspace_id)
        with self.metadata.transaction() as tx:
            return tx.list_runs(workspace_id)
    def start_run(self, workspace_id: UUID, document_ids: list[UUID] | None = None,
                  mode: str | None = None) -> Run:
        mode = mode or self.default_mode
        self._engine_for(mode)
        if not self._run_lock.acquire(blocking=False):
            raise KDAAError("run_busy", "Another run is active. Retry after it finishes.", 409)
        try:
            return self._execute_run(workspace_id, document_ids, mode)
        finally:
            self._run_lock.release()
    def _execute_run(self, workspace_id: UUID, document_ids: list[UUID] | None, mode: str) -> Run:
        workspace = self.get_workspace(workspace_id)
        documents = self.list_documents(workspace_id)
        if document_ids is not None:
            if len(document_ids) != len(set(document_ids)):
                raise KDAAError("duplicate_input_ids", "Select each document only once.")
            valid = {d.id for d in documents}
            if not set(document_ids) <= valid:
                raise KDAAError("invalid_run_sources", "Every selected document must belong to this workspace.")
            documents = [d for d in documents if d.id in document_ids]
        if not 1 <= len(documents) <= 20:
            raise KDAAError("run_size", "A run needs between 1 and 20 documents.")
        if sum(d.character_count for d in documents) > MAX_RUN_TEXT:
            raise KDAAError("run_size", "A run is limited to 250,000 normalized characters.", 413)
        engine = self._engine_for(mode)
        engine_version = getattr(engine, "engine_version", ENGINE_VERSION)
        config = {"mode": mode, "goal": workspace.goal, "focal_unit": workspace.focal_unit,
                  "engine_version": engine_version, "app_version": __version__,
                  "input_text_sha256": sha256("".join(sorted(d.text_sha256 for d in documents)).encode())}
        if mode == REFERENCE_MODE:
            config |= {"max_evidence_per_source_type": 3, "rules": [asdict(rule) for rule in RULES]}
        else:
            provider = getattr(engine, "provider", None)
            config |= {"provider": getattr(provider, "name", "unknown"),
                       "model": getattr(provider, "model", "unknown"),
                       "region": getattr(provider, "region", ""),
                       "simulated": bool(getattr(provider, "simulated", True)),
                       "prompt_versions": dict(PROMPT_VERSIONS),
                       "bounds": engine.bounds.as_config()}
        canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
        config_sha = sha256(canonical.encode())
        # Store exactly the JSON the hash covers, so a run reloaded from PostgreSQL equals the one just executed.
        config = json.loads(canonical)
        run = Run(workspace_id=workspace_id, config=config, config_sha256=config_sha,
            engine_version=engine_version,
            inputs=[SourceSnapshot(document_id=d.id, filename=d.filename, raw_sha256=d.raw_sha256,
                    text_sha256=d.text_sha256, normalization_version=d.normalization_version,
                    parser_version=d.parser_version) for d in documents])
        with self.metadata.transaction() as tx:
            tx.add_run(run)
            tx.add_event(self._event(run, "run.created", source_ids=[d.id for d in documents],
                                    details={"snapshot_saved": True, "mode": mode,
                                             "engine_version": engine_version}))
        stage = "startup"
        try:
            run = run.transition(RunStatus.RUNNING)
            with self.metadata.transaction() as tx:
                tx.update_run(run)
                tx.add_event(self._event(run, "run.started"))
            stage = "source_validation"
            texts = {d.id: self._text(d) for d in documents}
            with self.metadata.transaction() as tx:
                tx.add_event(self._event(run, "run.sources_read", source_ids=[d.id for d in documents],
                                        details={"hashes_verified": True}))
            stage = "reference_pipeline" if mode == REFERENCE_MODE else "live_pipeline"
            if mode == REFERENCE_MODE:
                bundle = engine.run(workspace, run, documents, texts)
            else:
                # Bodies go to the asset volume; provenance keeps keys, hashes and token usage.
                engine.recorder = self._record_interaction(run.id, [0])
                bundle = engine.run(workspace, run, documents, texts)
                self._record_live_stages(run, bundle)
            stage = "evidence_validation"
            validate_bundle(bundle, {d.id: d for d in documents}, texts, run.inputs)
            stage = "result_commit"
            completed = run.transition(RunStatus.SUCCEEDED, result=bundle)
            with self.metadata.transaction() as tx:
                tx.update_run(completed)
                tx.add_event(self._event(run, "discovery.completed", source_ids=[d.id for d in documents],
                    evidence_ids=[e.id for e in bundle.evidence], output_ids=[h.id for h in bundle.hypotheses],
                    details={"candidates": len(bundle.hypotheses), "mode": mode,
                             "duplicates_not_counted": bundle.duplicate_text_count,
                             "rejected_quotations": len(bundle.rejections)}))
                tx.add_event(self._event(run, "assessment.completed", output_ids=[h.id for h in bundle.hypotheses],
                    details={"exact_quote_validation": "passed", "ownership": "unresolved", "mode": mode,
                             "basis": "model_reading" if mode != REFERENCE_MODE else "lexical_rules"}))
                tx.add_event(self._event(run, "amplification.completed", evidence_ids=[e.id for e in bundle.evidence],
                    output_ids=[h.id for h in bundle.hypotheses],
                    details={"status": "proposal_only", "mode": mode,
                             "drafts": sum(1 for a in bundle.amplifications if a.draft)}))
                tx.add_event(self._event(run, "run.succeeded", output_ids=[run.id],
                    details={"mode": mode, "engine_version": engine_version}))
            return completed
        except Exception as exc:
            code = exc.code if isinstance(exc, KDAAError) else "run_failed"
            message = (exc.message if isinstance(exc, KDAAError)
                       else f"The {mode} run failed; no final results were committed.")
            # Never log prompts, model output or document text: only the failure class.
            logger.error("Run %s (%s mode) failed at %s (%s).", run.id, mode, stage, type(exc).__name__)
            with self.metadata.transaction() as tx:
                current = tx.get_run(run.id)
                if current is None:
                    raise
                failed = current.transition(RunStatus.FAILED, error={"code": code, "message": message, "stage": stage})
                tx.update_run(failed)
                tx.add_event(self._event(failed, "run.failed",
                    details={"stage": stage, "code": code, "mode": mode,
                             "retryable": bool(getattr(exc, "retryable", False)),
                             "fallback_used": False}))
            return failed
    def _record_live_stages(self, run: Run, bundle) -> None:
        """One event per model call and one per rejected quotation, so a stored live result can be
        audited without reading the recorded bodies."""
        events = []
        provider = bundle.provider
        for interaction in (provider.interactions if provider else []):
            events.append(self._event(run, "llm.call", details={
                "stage": interaction.stage, "prompt_version": interaction.prompt_version,
                "provider": provider.provider, "model": provider.model, "region": provider.region,
                "simulated": provider.simulated, "request_sha256": interaction.request_sha256,
                "response_sha256": interaction.response_sha256, "record_key": interaction.record_key,
                "stop_reason": interaction.stop_reason, "usage": interaction.usage,
                "latency_ms": interaction.latency_ms}))
        for rejection in bundle.rejections:
            events.append(self._event(run, "evidence.rejected",
                source_ids=[rejection.document_id] if rejection.document_id else [],
                details={"stage": rejection.stage, "reason": rejection.reason,
                         "quote_sha256": rejection.quote_sha256, "detail": rejection.detail,
                         "candidate_title": rejection.candidate_title}))
        if not events:
            return
        with self.metadata.transaction() as tx:
            for event in events:
                tx.add_event(event)
    @staticmethod
    def _event(run: Run, action: str, **kwargs) -> ProvenanceEvent:
        return ProvenanceEvent(workspace_id=run.workspace_id, run_id=run.id, action=action,
                               engine_version=run.engine_version, config_sha256=run.config_sha256, **kwargs)
    def recover_interrupted(self) -> int:
        """Startup only. Valid for exactly one API process owning this local database."""
        with self.metadata.transaction() as tx:
            unfinished = tx.unfinished_runs()
            for run in unfinished:
                interrupted = run.transition(RunStatus.INTERRUPTED,
                    error={"code": "process_restarted", "message": "The process ended before completion. Start a new run."})
                tx.update_run(interrupted)
                tx.add_event(self._event(interrupted, "run.interrupted", actor="system"))
        return len(unfinished)
    def get_provenance(self, run_id: UUID) -> list[ProvenanceEvent]:
        run = self.get_run(run_id)
        selected = {s.document_id for s in run.inputs}
        with self.metadata.transaction() as tx:
            all_events = tx.list_events(run.workspace_id)
        return [e for e in all_events if e.run_id == run_id or
                (e.run_id is None and (e.action == "workspace.created" or bool(set(e.source_ids) & selected)))]
    def workspace_provenance(self, workspace_id: UUID) -> list[ProvenanceEvent]:
        self.get_workspace(workspace_id)
        with self.metadata.transaction() as tx:
            return tx.list_events(workspace_id)
    def inspect_evidence(self, run_id: UUID, evidence_id: UUID) -> dict:
        run = self.get_run(run_id)
        evidence = next((e for e in run.result.evidence if e.id == evidence_id), None) if run.result else None
        if evidence is None:
            raise NotFound("Evidence")
        document = self._document(evidence.document_id)
        text = self._text(document)
        validate_evidence(evidence, document, text, run.workspace_id, run.id)
        with self.metadata.transaction() as tx:
            tx.add_event(self._event(run, "evidence.read", source_ids=[document.id], evidence_ids=[evidence.id]))
        return {"evidence": evidence, "filename": document.filename,
                "before": text[max(0, evidence.start-180):evidence.start],
                "quote": text[evidence.start:evidence.end], "after": text[evidence.end:evidence.end+180],
                "locator_unit": "zero-based, end-exclusive Unicode code points in normalized text",
                "verified": True}
    def export_run(self, run_id: UUID, format: str) -> tuple[str, str]:
        if format not in {"json", "markdown"}:
            raise KDAAError("export_format", "Choose json or markdown.")
        run = self.get_run(run_id)
        documents = [self._document(s.document_id) for s in run.inputs]
        texts = {d.id: self._text(d) for d in documents}
        if run.result:
            validate_bundle(run.result, {d.id: d for d in documents}, texts, run.inputs)
        with self.metadata.transaction() as tx:
            tx.add_event(self._event(run, "run.exported", source_ids=[d.id for d in documents], details={"format": format}))
        provenance = self.get_provenance(run.id)
        from .reports import markdown_report
        if format == "markdown":
            return markdown_report(run, provenance), "text/markdown"
        payload = {"schema_version": run.schema_version, "run": run.model_dump(mode="json"),
                   "sources": [{**d.model_dump(mode="json", exclude={"raw_key", "text_key"}),
                                "normalized_text": texts[d.id]} for d in documents],
                   "provenance": [e.model_dump(mode="json") for e in provenance],
                   "note": "Original binary files are not embedded. JSON includes normalized text for quote verification."}
        return json.dumps(payload, indent=2, ensure_ascii=False), "application/json"
    def demo_documents(self) -> list[dict[str, str]]:
        if self.demo_path is None or not self.demo_path.is_dir():
            raise KDAAError("demo_missing", "Demo documents are not installed.", 503)
        return [{"filename": p.name, "text": p.read_text(encoding="utf-8")}
                for p in sorted(self.demo_path.glob("*.md"))]
