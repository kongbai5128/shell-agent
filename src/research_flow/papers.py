from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from ..paths import ARTICLE_DIR, RESEARCH_PAPERS_DIR, ensure_using_dirs
from .library import ResearchLibrary, paper_id_from_name


CHUNK_SIZE = 1800
CHUNK_OVERLAP = 180
ARTICLE_EXTENSIONS = (".pdf", ".txt", ".md")


def find_local_article(query_or_paper_id: str) -> Path | None:
    ensure_using_dirs()
    query = query_or_paper_id.lower()
    candidates = [p for p in ARTICLE_DIR.iterdir() if p.is_file() and p.suffix.lower() in ARTICLE_EXTENSIONS]
    if not candidates:
        return None

    exact = [p for p in candidates if p.stem.lower() == query or p.name.lower() == query]
    if exact:
        return exact[0]

    contains = [p for p in candidates if query in p.stem.lower() or p.stem.lower() in query]
    if contains:
        return sorted(contains, key=lambda p: len(p.name))[0]
    return None


def _extract_pdf_text(path: Path) -> tuple[str, str]:
    """Best-effort PDF extraction. The original file body was not recovered."""
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        text = "\n".join(pages).strip()
        if text:
            return text, "pypdf"
    except Exception as exc:
        pypdf_error = str(exc)
    else:
        pypdf_error = "no text extracted"

    try:
        import subprocess

        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout, "pdftotext"
    except Exception:
        pass

    return (
        f"[PDF text extraction unavailable for {path.name}. Install pypdf or pdftotext. Last pypdf error: {pypdf_error}]",
        "unavailable",
    )


def _extract_article_text(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    return path.read_text(encoding="utf-8", errors="replace"), "text"


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def make_chunks(paper_id: str, text: str) -> list[dict]:
    return [
        {"paper_id": paper_id, "chunk_index": idx, "text": chunk}
        for idx, chunk in enumerate(_chunk_text(text))
    ]


def write_chunks(paper_id: str, text: str, paper_dir: Path | None = None) -> Path:
    """Deprecated compatibility shim: chunks are no longer written to disk."""
    paper_dir = paper_dir or (RESEARCH_PAPERS_DIR / paper_id)
    paper_dir.mkdir(parents=True, exist_ok=True)
    return paper_dir / "chunks.jsonl"


def read_chunks(paper_id: str) -> list[dict]:
    extracted_path = RESEARCH_PAPERS_DIR / paper_id / "extracted.txt"
    if not extracted_path.exists():
        return []
    text = extracted_path.read_text(encoding="utf-8", errors="replace")
    return make_chunks(paper_id, text)


def ensure_paper_in_article(
    query_or_paper_id: str,
    *,
    extract_text: bool = False,
    library: ResearchLibrary | None = None,
) -> dict:
    article = find_local_article(query_or_paper_id)
    if article is None:
        return {
            "ok": False,
            "error": f"No matching paper found under using/article for: {query_or_paper_id}",
            "article_dir": str(ARTICLE_DIR),
        }

    paper_id = paper_id_from_name(article.name)
    library = library or ResearchLibrary()
    metadata = {
        "ok": True,
        "paper_id": paper_id,
        "article_path": str(article),
        "article_name": article.name,
        "paper_dir": str(RESEARCH_PAPERS_DIR / paper_id),
    }

    if extract_text:
        text, method = _extract_article_text(article)
        paper_dir = RESEARCH_PAPERS_DIR / paper_id
        paper_dir.mkdir(parents=True, exist_ok=True)
        extracted_path = paper_dir / "extracted.txt"
        extracted_path.write_text(text, encoding="utf-8", errors="replace")
        chunks = make_chunks(paper_id, text)
        metadata.update(
            {
                "extract_method": method,
                "extracted_path": str(extracted_path),
                "chunk_count": len(chunks),
            }
        )

    library.upsert_paper(paper_id, metadata)
    return metadata


def ensure_paper_chunks(query_or_paper_id: str, *, library: ResearchLibrary | None = None) -> dict:
    paper_id = paper_id_from_name(query_or_paper_id)
    existing = read_chunks(paper_id)
    if existing:
        return {
            "ok": True,
            "paper_id": paper_id,
            "paper_dir": str(RESEARCH_PAPERS_DIR / paper_id),
            "chunk_count": len(existing),
            "created": False,
        }

    article = find_local_article(query_or_paper_id)
    if article is not None:
        return ensure_paper_in_article(article.name, extract_text=True, library=library)

    return {
        "ok": False,
        "paper_id": paper_id,
        "error": f"No chunks and no matching article under using/article for: {query_or_paper_id}",
    }


def chunks_text(chunks: Iterable[dict]) -> str:
    return "\n".join(str(row.get("text", "")) for row in chunks)
