from __future__ import annotations

from typing import Dict, List, Tuple
from time import perf_counter

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.document_store import DocumentStore
from app.engines.common import build_citations, fallback_answer, format_chunk
from app.llm_client import LLMClient
from app.models import AskResponse, Chunk


class LongContextEngine:
    MAX_DOCS_FOR_CONTEXT = 5
    MIN_CHUNKS_PER_DOC = 12
    DEPTH_BATCH_SIZE = 4

    def __init__(
        self,
        store: DocumentStore,
        llm: LLMClient,
        max_context_chars: int,
        max_context_tokens: int,
    ) -> None:
        self.store = store
        self.llm = llm
        self.max_context_chars = max_context_chars
        self.max_context_tokens = max_context_tokens

    def ask(self, question: str, top_k: int) -> AskResponse:
        start = perf_counter()
        chunks = sorted(self.store.all_chunks(), key=lambda c: (c.doc_name, c.page, c.ordinal))

        if not chunks:
            return AskResponse(
                mode="long_context",
                answer="No documents are loaded.",
                citations=[],
                latency_ms=int((perf_counter() - start) * 1000),
                context_chunks=0,
                context_chars=0,
                context_tokens=0,
            )

        selected, running_tokens = self._assemble_context(question, chunks)
        if not selected:
            selected = self._rank_relevant(question, chunks, max(1, top_k))
            running_tokens = self.llm.estimate_tokens("\n\n".join([format_chunk(c) for c in selected]), fast=True)

        context = "\n\n".join([format_chunk(c) for c in selected])

        answer = self.llm.answer("LONG_CONTEXT", question, context, use_cache=True)
        if "LLM is not configured" in answer or "Fallback mode" in answer:
            relevant = self._rank_relevant(question, selected, top_k)
            answer = "LLM fallback: %s\n\n%s" % (answer, fallback_answer(question, relevant))
            citation_source = relevant
        else:
            citation_source = self._rank_relevant(question, selected, top_k)

        response = AskResponse(
            mode="long_context",
            answer=answer,
            citations=build_citations(citation_source),
            latency_ms=int((perf_counter() - start) * 1000),
            context_chunks=len(selected),
            context_chars=len(context),
            context_tokens=running_tokens,
        )
        return response

    def _assemble_context(self, question: str, chunks: List[Chunk]) -> Tuple[List[Chunk], int]:
        scores = self._score_chunks(question, chunks)

        doc_to_chunks: Dict[str, List[Chunk]] = {}
        for chunk in chunks:
            if chunk.doc_name not in doc_to_chunks:
                doc_to_chunks[chunk.doc_name] = []
            doc_to_chunks[chunk.doc_name].append(chunk)

        ranked_docs = self._rank_documents(chunks, scores)[: self.MAX_DOCS_FOR_CONTEXT]

        selected: List[Chunk] = []
        running_chars = 0
        running_tokens = 0
        pointers = {doc: 0 for doc in ranked_docs}

        def add_next(doc_name: str) -> Tuple[bool, bool]:
            nonlocal running_chars, running_tokens
            chunk_list = doc_to_chunks[doc_name]
            index = pointers[doc_name]
            if index >= len(chunk_list):
                return False, False

            chunk = chunk_list[index]
            block = format_chunk(chunk)
            block_tokens = self.llm.estimate_tokens(block, fast=True)

            if running_chars + len(block) > self.max_context_chars:
                return False, True
            if running_tokens + block_tokens > self.max_context_tokens:
                return False, True

            selected.append(chunk)
            pointers[doc_name] = index + 1
            running_chars += len(block) + 2
            running_tokens += block_tokens
            return True, False

        for doc_name in ranked_docs:
            for _ in range(self.MIN_CHUNKS_PER_DOC):
                added, budget_hit = add_next(doc_name)
                if budget_hit:
                    return selected, running_tokens
                if not added:
                    break

        progress = True
        while progress:
            progress = False
            for doc_name in ranked_docs:
                for _ in range(self.DEPTH_BATCH_SIZE):
                    added, budget_hit = add_next(doc_name)
                    if budget_hit:
                        return selected, running_tokens
                    if not added:
                        break
                    progress = True

        return selected, running_tokens

    def _score_chunks(self, question: str, chunks: List[Chunk]) -> List[float]:
        try:
            vectorizer = TfidfVectorizer(stop_words="english")
            matrix = vectorizer.fit_transform([question] + [c.text for c in chunks])
        except ValueError:
            vectorizer = TfidfVectorizer()
            matrix = vectorizer.fit_transform([question] + [c.text for c in chunks])

        qv = matrix[0]
        dv = matrix[1:]
        raw = cosine_similarity(qv, dv)[0]
        return [float(x) for x in raw]

    def _rank_documents(self, chunks: List[Chunk], scores: List[float]) -> List[str]:
        grouped: Dict[str, List[float]] = {}
        doc_order: List[str] = []

        for chunk, score in zip(chunks, scores):
            name = chunk.doc_name
            if name not in grouped:
                grouped[name] = []
                doc_order.append(name)
            grouped[name].append(score)

        scored_docs: List[Tuple[str, float]] = []
        for doc_name, values in grouped.items():
            ordered = sorted(values, reverse=True)
            max_score = ordered[0] if ordered else 0.0
            top = ordered[:5]
            mean_top = (sum(top) / len(top)) if top else 0.0
            score = (max_score * 0.7) + (mean_top * 0.3)
            scored_docs.append((doc_name, score))

        if scored_docs and max(s for _, s in scored_docs) > 0:
            scored_sorted = sorted(scored_docs, key=lambda x: x[1], reverse=True)
            top_score = scored_sorted[0][1]
            threshold = top_score * 0.35
            filtered = [name for name, score in scored_sorted if score >= threshold and score > 0]
            if len(filtered) >= 3:
                return filtered
            return [name for name, _ in scored_sorted[:3]]

        return doc_order

    def _rank_relevant(self, question: str, chunks: List[Chunk], top_k: int) -> List[Chunk]:
        if not chunks:
            return []
        if len(chunks) <= top_k:
            return chunks

        try:
            vectorizer = TfidfVectorizer(stop_words="english")
            matrix = vectorizer.fit_transform([question] + [c.text for c in chunks])
        except ValueError:
            vectorizer = TfidfVectorizer()
            matrix = vectorizer.fit_transform([question] + [c.text for c in chunks])
        qv = matrix[0]
        dv = matrix[1:]
        scores = cosine_similarity(qv, dv)[0]
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [chunks[i] for i, _ in ranked[:top_k]]
