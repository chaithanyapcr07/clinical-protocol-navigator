from __future__ import annotations

import re
from typing import List, Set, Tuple

from app.models import Chunk, Citation


def format_chunk(chunk: Chunk) -> str:
    return (
        f"[{chunk.doc_name}|{chunk.page}|¶{chunk.paragraph_start}-{chunk.paragraph_end}|chunk:{chunk.ordinal}] "
        f"{chunk.text}"
    )


def build_citations(chunks: List[Chunk], max_items: int = 5) -> List[Citation]:
    citations: List[Citation] = []
    seen: Set[Tuple[str, int, int, int]] = set()

    for chunk in chunks:
        key = (chunk.doc_name, chunk.page, chunk.paragraph_start, chunk.paragraph_end)
        if key in seen:
            continue
        seen.add(key)
        snippet = re.sub(r"\s+", " ", chunk.text)[:220]
        citations.append(
            Citation(
                doc_name=chunk.doc_name,
                page=chunk.page,
                paragraph_start=chunk.paragraph_start,
                paragraph_end=chunk.paragraph_end,
                snippet=snippet,
            )
        )
        if len(citations) >= max_items:
            break

    return citations


def fallback_answer(question: str, chunks: List[Chunk]) -> str:
    if not chunks:
        return "No relevant content was found in uploaded documents."

    lines = [
        f"Question: {question}",
        "Summary from strongest matches:",
    ]
    for chunk in chunks[:3]:
        lines.append(
            f"- [{chunk.doc_name}|{chunk.page}|¶{chunk.paragraph_start}-{chunk.paragraph_end}] {chunk.text[:240]}"
        )

    return "\n".join(lines)
