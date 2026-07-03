from __future__ import annotations

import re
from pathlib import Path

from ..paths import ARTICLE_DIR, RESEARCH_PAPERS_DIR, ensure_using_dirs
from .library import ResearchLibrary, paper_id_from_name


# 正文只持久化为 extracted.txt；代码仓库 URL 直接从正文中本地提取。
ARTICLE_EXTENSIONS = (".pdf", ".txt", ".md")


def find_local_article(query_or_paper_id: str) -> Path | None:
    """在 using/article 中查找最匹配用户查询的本地论文文件。"""
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
    """优先用 pypdf 抽取 PDF 正文，失败后尝试系统 pdftotext。"""
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

    raise RuntimeError(
        f"PDF text extraction unavailable for {path.name}. "
        f"Install pypdf or pdftotext. Last pypdf error: {pypdf_error}"
    )


def _extract_article_text(path: Path) -> tuple[str, str]:
    """返回论文正文文本以及本次抽取使用的方法。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    return path.read_text(encoding="utf-8", errors="replace"), "text"


def read_extracted_text(paper_id: str) -> str:
    """读取已抽取的论文正文；不存在时返回空字符串。"""
    extracted_path = RESEARCH_PAPERS_DIR / paper_id / "extracted.txt"
    if not extracted_path.exists():
        return ""
    return extracted_path.read_text(encoding="utf-8", errors="replace")


def ensure_paper_in_article(
    query_or_paper_id: str,
    *,
    extract_text: bool = False,
    library: ResearchLibrary | None = None,
) -> dict:
    """解析本地论文文件，并按需持久化 extracted.txt 正文。"""
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
        try:
            text, method = _extract_article_text(article)
        except Exception as exc:
            metadata.update({"ok": False, "error": str(exc), "extract_method": "failed"})
            library.upsert_paper(paper_id, metadata)
            return metadata
        paper_dir = RESEARCH_PAPERS_DIR / paper_id
        paper_dir.mkdir(parents=True, exist_ok=True)
        extracted_path = paper_dir / "extracted.txt"
        extracted_path.write_text(text, encoding="utf-8", errors="replace")
        metadata.update(
            {
                "extract_method": method,
                "extracted_path": str(extracted_path),
                "text_chars": len(text),
            }
        )

    library.upsert_paper(paper_id, metadata)
    return metadata


def ensure_paper_text(query_or_paper_id: str, *, library: ResearchLibrary | None = None) -> dict:
    """确保论文正文已抽取到 extracted.txt，并返回正文基础信息。"""
    paper_id = paper_id_from_name(query_or_paper_id)
    existing_text = read_extracted_text(paper_id)
    if existing_text:
        return {
            "ok": True,
            "paper_id": paper_id,
            "paper_dir": str(RESEARCH_PAPERS_DIR / paper_id),
            "extracted_path": str(RESEARCH_PAPERS_DIR / paper_id / "extracted.txt"),
            "text_chars": len(existing_text),
            "created": False,
        }

    article = find_local_article(query_or_paper_id)
    if article is not None:
        return ensure_paper_in_article(article.name, extract_text=True, library=library)

    return {
        "ok": False,
        "paper_id": paper_id,
        "error": f"No extracted text and no matching article under using/article for: {query_or_paper_id}",
    }
