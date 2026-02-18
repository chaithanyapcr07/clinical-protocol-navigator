from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.audit_log import AuditLogger
from app.config import Settings, get_settings
from app.document_store import DocumentStore
from app.engines.long_context_engine import LongContextEngine
from app.engines.rag_engine import RAGEngine
from app.llm_client import LLMClient
from app.models import (
    AskRequest,
    AskResponse,
    AuditVerifyResponse,
    BenchmarkRequest,
    BenchmarkResponse,
    DocumentInfo,
    OpenClawAskRequest,
    OpenClawHandshakeResponse,
    OpenClawSyncRequest,
    OpenClawSyncResponse,
)
from app.security import RBACAuthorizer

settings = get_settings()
store = DocumentStore(upload_dir=Path("uploads"), enable_pii_redaction=settings.enable_pii_redaction)
store.load_existing_files()
if settings.openclaw_monitored_dir:
    monitored = Path(settings.openclaw_monitored_dir).expanduser()
    if monitored.exists() and monitored.is_dir():
        store.ingest_folder(monitored, allowed_extensions=set(settings.allowed_extensions()))

llm = LLMClient(settings)
rag_engine = RAGEngine(store, llm)


def _context_limits(cfg: Settings) -> Tuple[int, int]:
    profile = cfg.context_profile.strip().lower()
    if profile == "stress":
        return cfg.max_context_chars_stress, cfg.max_context_tokens_stress
    return cfg.max_context_chars, cfg.max_context_tokens


max_chars, max_tokens = _context_limits(settings)
long_context_engine = LongContextEngine(
    store,
    llm,
    max_context_chars=max_chars,
    max_context_tokens=max_tokens,
)

authorizer = RBACAuthorizer(settings)
audit_logger = AuditLogger(settings.audit_log_path, settings.audit_hash_seed)

app = FastAPI(title="Clinical Protocol Navigator", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _check_openclaw_secret(openclaw_secret: Optional[str]) -> None:
    expected = settings.openclaw_shared_secret.strip()
    if not expected:
        return
    if not openclaw_secret or openclaw_secret != expected:
        raise HTTPException(status_code=401, detail="Invalid OpenClaw secret")


def _extension_set(overrides: Optional[List[str]]) -> Set[str]:
    values = overrides if overrides else settings.allowed_extensions()
    return {x.strip().lower() for x in values if x and x.startswith(".")}


def _authorize(request: Request, permission: str, fallback_role: Optional[str] = None) -> str:
    role = request.headers.get(settings.rbac_header_name, "")
    if not role and fallback_role:
        role = fallback_role
    return authorizer.ensure(permission=permission, role_value=role)


def _audit(event_type: str, payload: Dict[str, Any]) -> None:
    try:
        audit_logger.append(event_type=event_type, payload=payload)
    except Exception:
        # Audit failures should not block request flow in this prototype.
        pass


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/api/health")
def health(request: Request) -> Dict[str, str]:
    _authorize(request, "read")
    return {"status": "ok"}


@app.get("/api/documents", response_model=List[DocumentInfo])
def list_documents(request: Request) -> List[DocumentInfo]:
    _authorize(request, "read")
    return store.list_documents()


@app.post("/api/documents/upload", response_model=List[DocumentInfo])
async def upload_documents(request: Request, files: List[UploadFile] = File(...)) -> List[DocumentInfo]:
    role = _authorize(request, "ingest")
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded")

    infos: List[DocumentInfo] = []
    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in {".pdf", ".txt", ".md"}:
            raise HTTPException(status_code=400, detail="Unsupported file type: %s" % upload.filename)

        original_name = upload.filename or "uploaded_document"
        safe_name = "%s_%s" % (uuid4().hex, original_name)
        path = Path("uploads") / safe_name
        content = await upload.read()
        path.write_bytes(content)
        infos.append(store.ingest_file(path, source_name=original_name))

    _audit(
        "documents_upload",
        {
            "role": role,
            "uploaded_count": len(infos),
            "documents": [x.model_dump() for x in infos],
        },
    )
    return infos


@app.post("/api/documents/reset")
def reset_documents(request: Request, delete_uploaded_files: bool = True) -> Dict[str, Any]:
    role = _authorize(request, "ingest")
    before = len(store.list_documents())
    store.clear(delete_uploaded_files=delete_uploaded_files)
    after = len(store.list_documents())
    response = {
        "status": "ok",
        "removed_documents": before,
        "remaining_documents": after,
        "delete_uploaded_files": delete_uploaded_files,
    }
    _audit(
        "documents_reset",
        {
            "role": role,
            "removed_documents": before,
            "remaining_documents": after,
            "delete_uploaded_files": delete_uploaded_files,
        },
    )
    return response


@app.post("/api/ask", response_model=AskResponse)
def ask(request: Request, payload: AskRequest) -> AskResponse:
    role = _authorize(request, "query")
    if payload.mode == "rag":
        response = rag_engine.ask(payload.question, payload.top_k)
    else:
        response = long_context_engine.ask(payload.question, payload.top_k)

    _audit(
        "ask",
        {
            "role": role,
            "mode": payload.mode,
            "top_k": payload.top_k,
            "question": payload.question,
            "latency_ms": response.latency_ms,
            "context_chunks": response.context_chunks,
            "context_tokens": response.context_tokens,
            "citations": [c.model_dump() for c in response.citations],
            "answer_excerpt": response.answer[:500],
        },
    )
    return response


@app.post("/api/benchmark", response_model=BenchmarkResponse)
def benchmark(request: Request, payload: BenchmarkRequest) -> BenchmarkResponse:
    role = _authorize(request, "query")
    rag = rag_engine.ask(payload.question, payload.top_k)
    if settings.benchmark_inter_mode_delay_seconds > 0:
        time.sleep(settings.benchmark_inter_mode_delay_seconds)
    long_context = long_context_engine.ask(payload.question, payload.top_k)
    response = BenchmarkResponse(question=payload.question, rag=rag, long_context=long_context)

    _audit(
        "benchmark",
        {
            "role": role,
            "top_k": payload.top_k,
            "question": payload.question,
            "rag": {
                "latency_ms": rag.latency_ms,
                "context_chunks": rag.context_chunks,
                "context_tokens": rag.context_tokens,
                "citations": [c.model_dump() for c in rag.citations],
                "fallback": rag.answer.startswith("LLM fallback:"),
            },
            "long_context": {
                "latency_ms": long_context.latency_ms,
                "context_chunks": long_context.context_chunks,
                "context_tokens": long_context.context_tokens,
                "citations": [c.model_dump() for c in long_context.citations],
                "fallback": long_context.answer.startswith("LLM fallback:"),
            },
        },
    )
    return response


@app.get("/api/openclaw/status")
def openclaw_status(request: Request) -> Dict[str, Any]:
    _authorize(request, "read")
    return {
        "enabled": True,
        "folder_sync_enabled": settings.openclaw_enable_folder_sync,
        "monitored_dir": settings.openclaw_monitored_dir,
        "allowed_extensions": settings.allowed_extensions(),
        "auth_required": bool(settings.openclaw_shared_secret.strip()),
        "context_profile": settings.context_profile,
        "max_context_chars": max_chars,
        "max_context_tokens": max_tokens,
        "benchmark_inter_mode_delay_seconds": settings.benchmark_inter_mode_delay_seconds,
    }


@app.get("/api/openclaw/handshake", response_model=OpenClawHandshakeResponse)
def openclaw_handshake(
    request: Request,
    x_openclaw_secret: Optional[str] = Header(default=None),
) -> OpenClawHandshakeResponse:
    _authorize(request, "read", fallback_role="admin")
    _check_openclaw_secret(x_openclaw_secret)
    monitored = settings.openclaw_monitored_dir or "(not configured)"
    return OpenClawHandshakeResponse(
        status="ok",
        monitored_dir=monitored,
        document_count=len(store.list_documents()),
        auth_required=bool(settings.openclaw_shared_secret.strip()),
    )


@app.post("/api/openclaw/sync-folder", response_model=OpenClawSyncResponse)
def openclaw_sync_folder(
    request: Request,
    payload: OpenClawSyncRequest,
    x_openclaw_secret: Optional[str] = Header(default=None),
) -> OpenClawSyncResponse:
    role = _authorize(request, "ingest", fallback_role="admin")
    _check_openclaw_secret(x_openclaw_secret)

    if not settings.openclaw_enable_folder_sync:
        raise HTTPException(status_code=403, detail="OpenClaw folder sync is disabled")

    folder_value = payload.folder_path or settings.openclaw_monitored_dir
    if not folder_value:
        raise HTTPException(status_code=400, detail="No folder_path provided and no OPENCLAW_MONITORED_DIR configured")

    folder = Path(folder_value).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail="Folder not found: %s" % folder)

    infos = store.ingest_folder(folder, allowed_extensions=_extension_set(payload.extensions))
    response = OpenClawSyncResponse(
        folder_path=str(folder),
        ingested_count=len(infos),
        documents=infos,
    )
    _audit(
        "openclaw_sync_folder",
        {
            "role": role,
            "folder_path": str(folder),
            "ingested_count": len(infos),
            "documents": [x.model_dump() for x in infos],
        },
    )
    return response


@app.post("/api/openclaw/ask")
def openclaw_ask(
    request: Request,
    payload: OpenClawAskRequest,
    x_openclaw_secret: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    role = _authorize(request, "query", fallback_role="admin")
    _check_openclaw_secret(x_openclaw_secret)

    if payload.benchmark:
        result = benchmark(request, BenchmarkRequest(question=payload.question, top_k=payload.top_k))
        _audit(
            "openclaw_ask_benchmark",
            {
                "role": role,
                "question": payload.question,
                "top_k": payload.top_k,
            },
        )
        return {"source": "openclaw", "result": result.model_dump()}

    result = ask(request, AskRequest(question=payload.question, mode=payload.mode, top_k=payload.top_k))
    _audit(
        "openclaw_ask",
        {
            "role": role,
            "question": payload.question,
            "mode": payload.mode,
            "top_k": payload.top_k,
            "latency_ms": result.latency_ms,
        },
    )
    return {"source": "openclaw", "result": result.model_dump()}


@app.get("/api/audit/verify", response_model=AuditVerifyResponse)
def audit_verify(request: Request) -> AuditVerifyResponse:
    _authorize(request, "admin", fallback_role="admin")
    result = audit_logger.verify()
    return AuditVerifyResponse(**result)
