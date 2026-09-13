import html
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID
from fastapi import FastAPI, File, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from kdaa import __version__
from kdaa.core.service import KDAAService
from kdaa.domain import DISCLAIMER, ENGINE_VERSION, Record, Run, RunStatus, Workspace, ProvenanceEvent, Evidence
from kdaa.errors import KDAAError
from kdaa.parsing import MAX_BYTES
from .config import LIVE_MODE, MODES, REFERENCE_MODE, Settings
from .engines import build_engines

class CreateWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    focal_unit: str = Field(default="My workspace", min_length=1, max_length=200)
    goal: str = Field(default="", max_length=2000)

class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[UUID] | None = Field(default=None, max_length=20)
    # Omitted means the server default. An unavailable mode is refused, never downgraded.
    mode: Literal["reference", "live"] | None = None

class DocumentView(Record):
    id: UUID
    workspace_id: UUID
    filename: str
    media_type: str
    raw_sha256: str
    text_sha256: str
    normalization_version: str
    parser_version: str
    byte_count: int
    character_count: int
    warnings: list[str]
    created_at: datetime

class RunSummary(Record):
    id: UUID
    workspace_id: UUID
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    engine_version: str
    candidate_count: int | None
    error: dict[str, str] | None

class NormalizedContent(Record):
    document_id: UUID
    filename: str
    text: str
    text_sha256: str
    normalization_version: str

class EvidenceView(Record):
    evidence: Evidence
    filename: str
    before: str
    quote: str
    after: str
    locator_unit: str
    verified: bool

class DemoDocument(Record):
    filename: str
    text: str

class LLMInfo(Record):
    """Non-secret configuration only. No credential, profile or token is ever reported."""
    enabled: bool
    provider: str
    model: str
    region: str
    max_input_chars: int
    max_output_tokens: int
    max_model_calls: int
    timeout_seconds: int
    total_attempts: int

class SystemInfo(Record):
    mode: Literal["reference", "live"]
    modes: list[str]
    engine: str
    live_llm: bool
    local_only: Literal[True]
    disclaimer: str
    version: str
    llm: LLMInfo

def create_app(service: KDAAService | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.validate()
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = None
        if service is None:
            from kdaa.providers.local import LocalAssetStore
            from kdaa.providers.postgres import PostgresMetadataStore
            owned = PostgresMetadataStore(settings.database_url)
            app.state.core = KDAAService(owned, LocalAssetStore(settings.asset_dir),
                                         engines=build_engines(settings), demo_path=settings.demo_dir,
                                         default_mode=settings.engine_mode)
        else:
            app.state.core = service
        try:
            if not app.state.core.ready():
                raise RuntimeError("Database migrations or source volume are not ready. Run the documented startup command.")
            app.state.core.recover_interrupted()
            yield
        finally:
            if owned:
                owned.close()
    app = FastAPI(title="KDAA local API", version=__version__, lifespan=lifespan,
                  description=DISCLAIMER, docs_url=None, redoc_url=None)
    hosts = ["localhost", "127.0.0.1", "backend"] + (["testserver"] if service is not None else [])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    @app.middleware("http")
    async def local_guard(request, call_next):
        origin = request.headers.get("origin")
        if ((origin is not None and origin not in settings.local_origins)
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse(status_code=403, content={"error": {"code": "local_only", "message": "Only approved local origins may use this unauthenticated PoC."}})
        length = request.headers.get("content-length")
        if length and length.isdecimal() and int(length) > MAX_BYTES + 128 * 1024:
            return JSONResponse(status_code=413, content={"error": {"code": "file_too_large", "message": "The upload limit is 5 MiB per file."}})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response
    @app.exception_handler(KDAAError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        issues = [{"location": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "The request did not match the API contract.", "issues": issues}})
    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logging.getLogger(__name__).error("Unhandled API error (%s).", type(exc).__name__)
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "The operation could not be completed. Check local service logs."}})
    def core() -> KDAAService:
        return app.state.core
    @app.get("/health")
    def health():
        return {"status": "alive", "version": __version__}
    @app.get("/ready")
    def ready():
        ok = core().ready()
        return JSONResponse(status_code=200 if ok else 503, content={"ready": ok})
    @app.get("/api/v1/system", response_model=SystemInfo)
    def system():
        modes = core().available_modes()
        live = LIVE_MODE in modes
        return {"mode": core().default_mode, "modes": modes, "engine": ENGINE_VERSION,
                "live_llm": live, "local_only": True, "disclaimer": DISCLAIMER, "version": __version__,
                "llm": {"enabled": live,
                        "provider": "aws-bedrock-converse" if live else "",
                        "model": settings.bedrock_model_id if live else "",
                        "region": settings.bedrock_region if live else "",
                        "max_input_chars": settings.llm_max_input_chars,
                        "max_output_tokens": settings.llm_max_output_tokens,
                        "max_model_calls": settings.llm_max_calls,
                        "timeout_seconds": settings.llm_timeout_seconds,
                        "total_attempts": settings.llm_total_attempts}}
    @app.get("/api/v1/workspaces", response_model=list[Workspace])
    def list_workspaces():
        return core().list_workspaces()
    @app.post("/api/v1/workspaces", response_model=Workspace, status_code=201)
    def create_workspace(body: CreateWorkspace):
        return core().create_workspace(**body.model_dump())
    @app.get("/api/v1/workspaces/{workspace_id}", response_model=Workspace)
    def get_workspace(workspace_id: UUID):
        return core().get_workspace(workspace_id)
    @app.get("/api/v1/workspaces/{workspace_id}/documents", response_model=list[DocumentView])
    def documents(workspace_id: UUID):
        return [d.model_dump(exclude={"raw_key", "text_key"}) for d in core().list_documents(workspace_id)]
    @app.post("/api/v1/workspaces/{workspace_id}/documents", response_model=DocumentView, status_code=201)
    async def upload(workspace_id: UUID, file: UploadFile = File(...)):
        try:
            content = await file.read(MAX_BYTES + 1)
            result = await run_in_threadpool(core().import_document, workspace_id, file.filename or "", content)
            return result.model_dump(exclude={"raw_key", "text_key"})
        finally:
            await file.close()
    @app.get("/api/v1/documents/{document_id}/content", response_model=NormalizedContent)
    def content(document_id: UUID):
        return core().read_document(document_id)
    @app.get("/api/v1/documents/{document_id}/download")
    def original(document_id: UUID):
        document, data = core().read_original(document_id)
        return Response(data, media_type=document.media_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(document.filename, safe='')}"})
    @app.post("/api/v1/workspaces/{workspace_id}/runs", response_model=Run, status_code=201)
    def run(workspace_id: UUID, body: StartRun):
        return core().start_run(workspace_id, body.document_ids, body.mode)
    @app.get("/api/v1/workspaces/{workspace_id}/runs", response_model=list[RunSummary])
    def runs(workspace_id: UUID):
        return [RunSummary(id=r.id, workspace_id=r.workspace_id, status=r.status, created_at=r.created_at,
            updated_at=r.updated_at, engine_version=r.engine_version,
            candidate_count=len(r.result.hypotheses) if r.result else None, error=r.error)
            for r in core().list_runs(workspace_id)]
    @app.get("/api/v1/runs/{run_id}", response_model=Run)
    def get_run(run_id: UUID):
        return core().get_run(run_id)
    @app.get("/api/v1/runs/{run_id}/provenance", response_model=list[ProvenanceEvent])
    def provenance(run_id: UUID):
        return core().get_provenance(run_id)
    @app.get("/api/v1/workspaces/{workspace_id}/provenance", response_model=list[ProvenanceEvent])
    def workspace_provenance(workspace_id: UUID):
        return core().workspace_provenance(workspace_id)
    @app.get("/api/v1/runs/{run_id}/evidence/{evidence_id}", response_model=EvidenceView)
    def evidence(run_id: UUID, evidence_id: UUID):
        return core().inspect_evidence(run_id, evidence_id)
    @app.get("/api/v1/runs/{run_id}/export")
    def export(run_id: UUID, format: Literal["json", "markdown"] = "json"):
        text, media_type = core().export_run(run_id, format)
        ext = "json" if format == "json" else "md"
        return Response(text, media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="kdaa-run-{run_id}.{ext}"'})
    @app.get("/api/v1/demo", response_model=list[DemoDocument])
    def demo():
        # Fixtures only. The UI submits them through the ordinary document-import endpoint.
        return core().demo_documents()
    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    def docs():
        schema = app.openapi()
        rows = []
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                rows.append(f"<tr><td>{html.escape(method.upper())}</td><td><code>{html.escape(path)}</code></td><td>{html.escape(operation.get('summary',''))}</td></tr>")
        return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KDAA API</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#243447}td{padding:12px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;font-size:12px}a{color:#08766b}</style><h1>KDAA local API</h1><p>Offline API reference. No external scripts or model calls.</p><p><a href="/openapi.json">OpenAPI JSON</a></p><table>""" + "".join(rows) + "</table><details><summary>Full schema</summary><pre>" + html.escape(json.dumps(schema, indent=2)) + "</pre></details></html>"
    return app

app = create_app()
