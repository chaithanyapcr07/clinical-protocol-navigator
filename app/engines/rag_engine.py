from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, List, Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.document_store import DocumentStore
from app.engines.common import build_citations, fallback_answer, format_chunk
from app.llm_client import LLMClient
from app.models import AskResponse, Chunk


@dataclass
class _IndexCache:
    version: int = -1
    vectorizer: Optional[TfidfVectorizer] = None
    matrix: Optional[Any] = None


class RAGEngine:
    def __init__(self, store: DocumentStore, llm: LLMClient) -> None:
        self.store = store
        self.llm = llm
        self.cache = _IndexCache()

    def ask(self, question: str, top_k: int) -> AskResponse:
        start = perf_counter()
        chunks = self.store.all_chunks()
        if not chunks:
            return AskResponse(
                mode="rag",
                answer="No documents are loaded.",
                citations=[],
                latency_ms=int((perf_counter() - start) * 1000),
                context_chunks=0,
                context_chars=0,
                context_tokens=0,
            )

        self._ensure_index(chunks)
        query_vector = self.cache.vectorizer.transform([question])
        scores = cosine_similarity(query_vector, self.cache.matrix)[0]
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        selected = [chunks[idx] for idx, score in ranked[:top_k] if score > 0]
        if not selected:
            selected = [chunks[idx] for idx, _ in ranked[:top_k]]

        context_blocks = [format_chunk(c) for c in selected]
        context = "\n\n".join(context_blocks)
        context_tokens = self.llm.estimate_tokens(context, fast=True)

        answer = self.llm.answer("RAG", question, context)
        if "LLM is not configured" in answer or "Fallback mode" in answer:
            answer = "LLM fallback: %s\n\n%s" % (answer, fallback_answer(question, selected))

        response = AskResponse(
            mode="rag",
            answer=answer,
            citations=build_citations(selected),
            latency_ms=int((perf_counter() - start) * 1000),
            context_chunks=len(selected),
            context_chars=len(context),
            context_tokens=context_tokens,
        )
        return response

    def _ensure_index(self, chunks: List[Chunk]) -> None:
        if self.cache.version == self.store.version and self.cache.matrix is not None:
            return

        texts = [c.text for c in chunks]
        try:
            vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
            matrix = vectorizer.fit_transform(texts)
        except ValueError:
            # Handles corpora that reduce to stop words only.
            vectorizer = TfidfVectorizer(ngram_range=(1, 2))
            matrix = vectorizer.fit_transform(texts)

        self.cache.version = self.store.version
        self.cache.vectorizer = vectorizer
        self.cache.matrix = matrix
