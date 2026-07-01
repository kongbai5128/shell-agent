from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from . import ToolSpec, register
from ..paths import ARTICLE_DIR, RESEARCH_PAPERS_DIR, ensure_using_dirs
from ..research_flow import (
    ResearchLibrary,
    ensure_paper_chunks,
    ensure_paper_in_article,
    find_code_candidates,
    prepare_reproduction_from_request,
    prepare_reproduction_from_paper,
    read_chunks,
)


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _research_resolve_paper(inp: dict) -> str:
    query = inp.get("query") or inp.get("paper_id") or ""
    if not query:
        return "Missing query"
    article = ensure_paper_in_article(query, extract_text=False)
    if article.get("ok"):
        return _json(article)
    matches = ResearchLibrary().search(query)
    return _json({"ok": bool(matches), "query": query, "library_matches": matches, "article_error": article})


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "shell-agent/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        dest.write_bytes(response.read())


def _research_fetch_paper(inp: dict) -> str:
    ensure_using_dirs()
    url = inp.get("url") or inp.get("query") or ""
    name = inp.get("filename") or ""
    if not url:
        return "Missing url"
    if "arxiv.org/abs/" in url:
        arxiv_id = url.rstrip("/").split("/")[-1]
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    if not name:
        suffix = ".pdf" if url.lower().endswith(".pdf") or "arxiv.org/pdf/" in url else ".txt"
        name = url.rstrip("/").split("/")[-1] or f"paper{suffix}"
        if not Path(name).suffix:
            name += suffix
    dest = ARTICLE_DIR / Path(name).name
    try:
        _download(url, dest)
    except Exception as exc:
        return _json({"ok": False, "url": url, "error": str(exc), "article_dir": str(ARTICLE_DIR)})
    return _json({"ok": True, "url": url, "path": str(dest)})


def _research_create_reading_note(inp: dict) -> str:
    query = inp.get("query") or inp.get("paper_id") or ""
    info = ensure_paper_chunks(query)
    if not info.get("ok"):
        return _json(info)
    paper_id = info["paper_id"]
    first_chunks = read_chunks(paper_id)[:5]
    note_path = RESEARCH_PAPERS_DIR / paper_id / "reading_note.md"
    note_path.write_text(
        "\n".join(
            [
                f"# Reading Note: {paper_id}",
                "",
                "Generated from recovered/reconstructed research workflow.",
                "",
                "## Evidence Chunks",
                *[f"- {chunk.get('text', '')[:500]}" for chunk in first_chunks],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return _json({"ok": True, "paper_id": paper_id, "note_path": str(note_path)})


def _research_find_code(inp: dict) -> str:
    query = inp.get("query") or inp.get("paper_id") or ""
    return _json(find_code_candidates(query))


def _research_reproduction_plan(inp: dict) -> str:
    query = inp.get("query") or inp.get("paper_id") or ""
    result = prepare_reproduction_from_paper(query, clone=False)
    return _json(result)


def _research_prepare_from_paper(inp: dict) -> str:
    query = inp.get("query") or inp.get("paper_id") or ""
    clone = bool(inp.get("clone", True))
    result = prepare_reproduction_from_paper(query, clone=clone)
    return _json(result)


def _research_prepare_reproduction(inp: dict, engine=None) -> str:
    request = inp.get("request") or inp.get("query") or inp.get("paper_id") or ""
    if not request:
        return "Missing request"
    clone = bool(inp.get("clone", True))
    allow_web = bool(inp.get("allow_web", True))
    result = prepare_reproduction_from_request(
        request,
        clone=clone,
        allow_web=allow_web,
        engine=engine,
    )
    return _json(result)


def _research_search_library(inp: dict) -> str:
    query = inp.get("query") or ""
    return _json({"ok": True, "query": query, "matches": ResearchLibrary().search(query)})


_COMMON_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Paper title, local filename, paper id, or URL."},
        "paper_id": {"type": "string", "description": "Existing paper id under using/research/papers."},
    },
}


register(ToolSpec(
    name="research_resolve_paper",
    description="Resolve a paper from using/article or the research library index.",
    input_schema=_COMMON_QUERY_SCHEMA,
    handler=_research_resolve_paper,
))

register(ToolSpec(
    name="research_fetch_paper",
    description="Download a paper into using/article. Supports direct PDF URLs and arXiv abs URLs.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Paper URL."},
            "query": {"type": "string", "description": "Alias for url."},
            "filename": {"type": "string", "description": "Optional local filename."},
        },
    },
    handler=_research_fetch_paper,
))

register(ToolSpec(
    name="research_create_reading_note",
    description="Create a reading note from existing or newly-created paper chunks.",
    input_schema=_COMMON_QUERY_SCHEMA,
    handler=_research_create_reading_note,
))

register(ToolSpec(
    name="research_find_code",
    description="Search paper chunks for Git repository URLs and write code_candidates.json.",
    input_schema=_COMMON_QUERY_SCHEMA,
    handler=_research_find_code,
))

register(ToolSpec(
    name="research_reproduction_plan",
    description="Create a reproduction plan from paper chunks without cloning code.",
    input_schema=_COMMON_QUERY_SCHEMA,
    handler=_research_reproduction_plan,
))

register(ToolSpec(
    name="research_prepare_from_paper",
    description="Prepare reproduction assets and optionally clone candidate code from a paper.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Paper title, local filename, or paper id."},
            "paper_id": {"type": "string", "description": "Existing paper id."},
            "clone": {"type": "boolean", "description": "Clone the first Git candidate, default true."},
        },
    },
    handler=_research_prepare_from_paper,
    dangerous=True,
))

register(ToolSpec(
    name="research_prepare_reproduction",
    description=(
        "End-to-end research reproduction workflow from a natural-language request: "
        "choose the most relevant paper from using/article with the current model; "
        "if missing, search the web and fetch a paper; extract chunks into using/research; "
        "find Git repository URLs in chunks or web results; clone the best repository when found; "
        "return no_repository_found when no code URL exists."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "request": {"type": "string", "description": "Natural-language request, e.g. '复现 hnsw 代码'."},
            "query": {"type": "string", "description": "Alias for request."},
            "paper_id": {"type": "string", "description": "Optional known paper id."},
            "clone": {"type": "boolean", "description": "Clone the best Git candidate, default true."},
            "allow_web": {"type": "boolean", "description": "Search the web if using/article has no relevant paper, default true."},
        },
        "required": ["request"],
    },
    handler=_research_prepare_reproduction,
    dangerous=True,
))

register(ToolSpec(
    name="research_search_library",
    description="Search using/research/index.json for known papers.",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    handler=_research_search_library,
))
