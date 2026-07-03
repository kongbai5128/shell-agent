from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from . import ToolSpec, register
from ..paths import ARTICLE_DIR, RESEARCH_PAPERS_DIR, ensure_using_dirs
from ..research_flow import (
    ResearchLibrary,
    ensure_paper_in_article,
    find_code_candidates,
    prepare_reproduction_from_request,
    prepare_reproduction_from_paper,
)


def _json(data: dict) -> str:
    """将工具返回渲染为模型易读的 JSON 字符串。"""
    return json.dumps(data, ensure_ascii=False, indent=2)


def _research_resolve_paper(inp: dict) -> str:
    """从 using/article 或轻量研究索引中解析论文。"""
    query = inp.get("query") or inp.get("paper_id") or ""
    if not query:
        return "缺少 query"
    article = ensure_paper_in_article(query, extract_text=False)
    if article.get("ok"):
        return _json(article)
    matches = ResearchLibrary().search(query)
    return _json({"ok": bool(matches), "query": query, "library_matches": matches, "article_error": article})


def _download(url: str, dest: Path) -> None:
    """下载论文文件到本地 article 目录。"""
    req = urllib.request.Request(url, headers={"User-Agent": "shell-agent/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        dest.write_bytes(response.read())


def _research_fetch_paper(inp: dict) -> str:
    """将 PDF/TXT 论文获取到 using/article，供后续复现流程使用。"""
    ensure_using_dirs()
    url = inp.get("url") or inp.get("query") or ""
    name = inp.get("filename") or ""
    if not url:
        return "缺少 url"
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


def _research_find_code(inp: dict) -> str:
    """返回论文正文中找到的 Git 仓库候选。"""
    query = inp.get("query") or inp.get("paper_id") or ""
    return _json(find_code_candidates(query))


def _research_reproduction_plan(inp: dict, engine=None) -> str:
    """构建复现计划，但不 clone 候选仓库。"""
    query = inp.get("query") or inp.get("paper_id") or ""
    result = prepare_reproduction_from_paper(query, clone=False, setup_env=False, request=query, engine=engine)
    return _json(result)


def _research_prepare_from_paper(inp: dict, engine=None) -> str:
    """基于明确指定的论文准备复现资源。"""
    query = inp.get("query") or inp.get("paper_id") or ""
    clone = bool(inp.get("clone", True))
    setup_env = bool(inp.get("setup_env", True))
    result = prepare_reproduction_from_paper(query, clone=clone, setup_env=setup_env, request=query, engine=engine)
    return _json(result)


def _research_prepare_reproduction(inp: dict, engine=None) -> str:
    """端到端执行自然语言复现需求流程。"""
    request = inp.get("request") or inp.get("query") or inp.get("paper_id") or ""
    if not request:
        return "缺少 request"
    clone = bool(inp.get("clone", True))
    setup_env = bool(inp.get("setup_env", True))
    allow_web = bool(inp.get("allow_web", True))
    result = prepare_reproduction_from_request(
        request,
        clone=clone,
        setup_env=setup_env,
        allow_web=allow_web,
        engine=engine,
    )
    return _json(result)


def _research_search_library(inp: dict) -> str:
    """检索紧凑的 using/research/index.json 论文索引。"""
    query = inp.get("query") or ""
    return _json({"ok": True, "query": query, "matches": ResearchLibrary().search(query)})


_COMMON_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "论文标题、本地文件名、论文 ID 或 URL。"},
        "paper_id": {"type": "string", "description": "using/research/papers 下已有的论文 ID。"},
    },
}


register(ToolSpec(
    name="research_resolve_paper",
    description="从 using/article 或 research 索引中解析论文。",
    input_schema=_COMMON_QUERY_SCHEMA,
    handler=_research_resolve_paper,
))

register(ToolSpec(
    name="research_fetch_paper",
    description="下载论文到 using/article，支持直接 PDF URL 和 arXiv abs URL。",
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "论文 URL。"},
            "query": {"type": "string", "description": "url 的别名。"},
            "filename": {"type": "string", "description": "可选的本地文件名。"},
        },
    },
    handler=_research_fetch_paper,
))

register(ToolSpec(
    name="research_find_code",
    description="在论文正文中查找 Git 仓库 URL，并直接返回候选结果，不返回论文全文。",
    input_schema=_COMMON_QUERY_SCHEMA,
    handler=_research_find_code,
))

register(ToolSpec(
    name="research_reproduction_plan",
    description="基于论文正文中的 Git 仓库 URL 创建复现计划和后续启动命令，但不 clone 代码。",
    input_schema=_COMMON_QUERY_SCHEMA,
    handler=_research_reproduction_plan,
))

register(ToolSpec(
    name="research_prepare_from_paper",
    description="基于指定论文准备复现资源、安装 conda 环境、输出后续启动命令，并可选择 clone 候选代码仓库。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "论文标题、本地文件名或论文 ID。"},
            "paper_id": {"type": "string", "description": "已有论文 ID。"},
            "clone": {"type": "boolean", "description": "是否 clone 最合适的 Git 候选仓库，默认 true。"},
            "setup_env": {"type": "boolean", "description": "是否创建 conda 环境并安装依赖，默认 true。"},
        },
    },
    handler=_research_prepare_from_paper,
    dangerous=True,
))

register(ToolSpec(
    name="research_prepare_reproduction",
    description=(
        "从自然语言需求端到端准备论文复现："
        "先用当前模型从 using/article 选择最相关论文；"
        "本地没有时联网搜索并获取论文；抽取正文到 extracted.txt；"
        "只从论文正文中寻找 Git 仓库 URL；找到后 clone 最合适仓库；"
        "默认创建 conda 环境并安装依赖，输出 run_steps 供用户后续自己启动；"
        "论文正文中没有代码 URL 时返回 no_repository_found。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "request": {"type": "string", "description": "自然语言复现需求，例如“复现 hnsw 代码”。"},
            "query": {"type": "string", "description": "request 的别名。"},
            "paper_id": {"type": "string", "description": "可选的已知论文 ID。"},
            "clone": {"type": "boolean", "description": "是否 clone 最合适的 Git 候选仓库，默认 true。"},
            "setup_env": {"type": "boolean", "description": "是否创建 conda 环境并安装依赖，默认 true。"},
            "allow_web": {"type": "boolean", "description": "using/article 没有相关论文时是否联网搜索，默认 true。"},
        },
        "required": ["request"],
    },
    handler=_research_prepare_reproduction,
    dangerous=True,
))

register(ToolSpec(
    name="research_search_library",
    description="在 using/research/index.json 中检索已知论文。",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    handler=_research_search_library,
))
