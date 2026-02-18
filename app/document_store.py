from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from PyPDF2 import PdfReader

from app.models import Chunk, DocumentInfo


class DocumentStore:
    def __init__(self, upload_dir: Path, enable_pii_redaction: bool = True) -> None:
        self.upload_dir = upload_dir
        self.upload_dir.mkdir(parents=True, exist_ok=True)

        self.enable_pii_redaction = enable_pii_redaction
        self._chunks: List[Chunk] = []
        self._docs: Dict[str, DocumentInfo] = {}
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    def list_documents(self) -> List[DocumentInfo]:
        return list(self._docs.values())

    def all_chunks(self) -> List[Chunk]:
        return self._chunks

    def ingest_file(self, file_path: Path, source_name: Optional[str] = None) -> DocumentInfo:
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            pages = self._extract_pdf_pages(file_path)
        else:
            pages = self._extract_text_pages(file_path)

        display_name = source_name or file_path.name
        doc_id = self._doc_id(display_name)
        if doc_id in self._docs:
            self._remove_doc(doc_id)

        doc_chunks: List[Chunk] = []
        ordinal = 0

        for page_no, page_text in enumerate(pages, start=1):
            paragraphs = self._split_page_paragraphs(page_text)
            if not paragraphs:
                continue

            if self.enable_pii_redaction:
                paragraphs = [self._redact_pii(p) for p in paragraphs]

            for text, para_start, para_end in self._chunk_paragraphs(paragraphs):
                chunk = Chunk(
                    chunk_id="%s:%s" % (doc_id, ordinal),
                    doc_id=doc_id,
                    doc_name=display_name,
                    page=page_no,
                    paragraph_start=para_start,
                    paragraph_end=para_end,
                    ordinal=ordinal,
                    text=text,
                )
                doc_chunks.append(chunk)
                ordinal += 1

        self._chunks.extend(doc_chunks)
        info = DocumentInfo(doc_id=doc_id, doc_name=display_name, pages=len(pages), chunks=len(doc_chunks))
        self._docs[doc_id] = info
        self._version += 1
        return info

    def load_existing_files(self) -> None:
        for file_path in sorted(self.upload_dir.iterdir()):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {".pdf", ".txt", ".md"}:
                continue
            self.ingest_file(file_path, source_name=file_path.name)

    def ingest_folder(self, folder_path: Path, allowed_extensions: Optional[Set[str]] = None) -> List[DocumentInfo]:
        if not folder_path.exists() or not folder_path.is_dir():
            return []

        if allowed_extensions is None:
            allowed_extensions = {".pdf", ".txt", ".md"}

        infos: List[DocumentInfo] = []
        for file_path in sorted(folder_path.iterdir()):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in allowed_extensions:
                continue
            infos.append(self.ingest_file(file_path, source_name=file_path.name))
        return infos

    def clear(self, delete_uploaded_files: bool = True) -> None:
        self._chunks = []
        self._docs = {}
        if delete_uploaded_files:
            for file_path in sorted(self.upload_dir.iterdir()):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in {".pdf", ".txt", ".md"}:
                    continue
                try:
                    file_path.unlink()
                except Exception:
                    continue
        self._version += 1

    def _remove_doc(self, doc_id: str) -> None:
        self._chunks = [c for c in self._chunks if c.doc_id != doc_id]
        self._docs.pop(doc_id, None)

    def _doc_id(self, source_name: str) -> str:
        digest = hashlib.sha256(source_name.encode("utf-8")).hexdigest()
        return digest[:12]

    def _extract_pdf_pages(self, file_path: Path) -> List[str]:
        reader = PdfReader(str(file_path))
        pages: List[str] = []
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            pages.append(text)
        return pages or [""]

    def _extract_text_pages(self, file_path: Path) -> List[str]:
        raw = file_path.read_text(encoding="utf-8", errors="ignore")
        sections = re.split(r"\n\s*\n", raw)
        pages: List[str] = []
        current: List[str] = []
        char_count = 0
        for section in sections:
            size = len(section)
            if char_count + size > 3500 and current:
                pages.append("\n\n".join(current))
                current = [section]
                char_count = size
            else:
                current.append(section)
                char_count += size
        if current:
            pages.append("\n\n".join(current))
        return pages or [raw]

    def _split_page_paragraphs(self, page_text: str) -> List[str]:
        if not page_text:
            return []

        raw = page_text.replace("\r", "\n")
        blocks = [x.strip() for x in re.split(r"\n\s*\n+", raw) if x.strip()]

        if len(blocks) <= 1:
            lines = [x.strip() for x in raw.splitlines() if x.strip()]
            if not lines:
                return []
            stitched: List[str] = []
            bucket: List[str] = []
            for line in lines:
                bucket.append(line)
                joined = " ".join(bucket)
                if joined.endswith((".", ";", ":")) and len(joined) >= 240:
                    stitched.append(joined)
                    bucket = []
            if bucket:
                stitched.append(" ".join(bucket))
            blocks = stitched

        normalized = [self._normalize_spaces(x) for x in blocks]
        return [x for x in normalized if x]

    def _chunk_paragraphs(
        self,
        paragraphs: Sequence[str],
        chunk_size: int = 1400,
    ) -> List[Tuple[str, int, int]]:
        chunks: List[Tuple[str, int, int]] = []

        buffer: List[str] = []
        buffer_start = 0
        buffer_end = 0
        current_len = 0

        def flush() -> None:
            nonlocal buffer, buffer_start, buffer_end, current_len
            if not buffer:
                return
            chunks.append(("\n\n".join(buffer), buffer_start, buffer_end))
            buffer = []
            buffer_start = 0
            buffer_end = 0
            current_len = 0

        for idx, paragraph in enumerate(paragraphs, start=1):
            if not paragraph:
                continue

            if len(paragraph) > int(chunk_size * 1.3):
                flush()
                for piece in self._split_long_paragraph(paragraph, chunk_size):
                    chunks.append((piece, idx, idx))
                continue

            additional = len(paragraph) + (2 if buffer else 0)
            if buffer and current_len + additional > chunk_size:
                flush()

            if not buffer:
                buffer_start = idx
            buffer.append(paragraph)
            buffer_end = idx
            current_len += additional

        flush()
        return chunks

    def _split_long_paragraph(self, paragraph: str, chunk_size: int) -> List[str]:
        sentence_parts = re.split(r"(?<=[.!?])\s+", paragraph)
        pieces: List[str] = []
        current = ""

        for sentence in sentence_parts:
            candidate = (current + " " + sentence).strip() if current else sentence
            if current and len(candidate) > chunk_size:
                pieces.append(current.strip())
                current = sentence
            else:
                current = candidate

        if current:
            pieces.append(current.strip())

        final_pieces: List[str] = []
        for piece in pieces:
            if len(piece) <= chunk_size:
                final_pieces.append(piece)
                continue
            start = 0
            while start < len(piece):
                end = min(start + chunk_size, len(piece))
                final_pieces.append(piece[start:end].strip())
                if end >= len(piece):
                    break
                start = end

        return [x for x in final_pieces if x]

    def _normalize_spaces(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _redact_pii(self, text: str) -> str:
        redacted = text
        redacted = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]", redacted)
        redacted = re.sub(
            r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
            "[REDACTED_PHONE]",
            redacted,
        )
        redacted = re.sub(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            "[REDACTED_EMAIL]",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            r"\b(?:MRN|Medical\s*Record\s*Number)\s*[:#]?\s*[A-Z0-9-]{4,}\b",
            "MRN [REDACTED]",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            r"\b(?:DOB|Date\s*of\s*Birth)\s*[:#]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
            "DOB [REDACTED]",
            redacted,
            flags=re.IGNORECASE,
        )
        return redacted
