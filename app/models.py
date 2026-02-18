from dataclasses import dataclass
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


Mode = Literal["rag", "long_context"]


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_name: str
    page: int
    paragraph_start: int
    paragraph_end: int
    ordinal: int
    text: str


class Citation(BaseModel):
    doc_name: str
    page: int
    paragraph_start: Optional[int] = None
    paragraph_end: Optional[int] = None
    snippet: str


class AskRequest(BaseModel):
    question: str = Field(min_length=3)
    mode: Mode = "rag"
    top_k: int = Field(default=8, ge=1, le=20)


class AskResponse(BaseModel):
    mode: Mode
    answer: str
    citations: List[Citation]
    latency_ms: int
    context_chunks: int
    context_chars: int
    context_tokens: int = 0


class BenchmarkRequest(BaseModel):
    question: str = Field(min_length=3)
    top_k: int = Field(default=8, ge=1, le=20)


class BenchmarkResponse(BaseModel):
    question: str
    rag: AskResponse
    long_context: AskResponse


class DocumentInfo(BaseModel):
    doc_id: str
    doc_name: str
    pages: int
    chunks: int


class OpenClawSyncRequest(BaseModel):
    folder_path: Optional[str] = None
    extensions: Optional[List[str]] = None


class OpenClawAskRequest(BaseModel):
    question: str = Field(min_length=3)
    mode: Mode = "long_context"
    top_k: int = Field(default=8, ge=1, le=20)
    benchmark: bool = False


class OpenClawSyncResponse(BaseModel):
    folder_path: str
    ingested_count: int
    documents: List[DocumentInfo]


class OpenClawHandshakeResponse(BaseModel):
    status: str
    monitored_dir: str
    document_count: int
    auth_required: bool


class AuditVerifyResponse(BaseModel):
    ok: bool
    entries: int
    last_hash: str
    error: Optional[str] = None
