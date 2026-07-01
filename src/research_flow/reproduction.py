from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ..paths import ARTICLE_DIR, RESEARCH_PAPERS_DIR, ensure_using_dirs
from .library import ResearchLibrary, paper_id_from_name
from .papers import ARTICLE_EXTENSIONS, chunks_text, ensure_paper_chunks, read_chunks


GIT_RE = re.compile(r"(?:https?://)?(?:github\.com|gitlab\.com|bitbucket\.org)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?")
GITHUB_RE = GIT_RE
ARXIV_ABS_RE = re.compile(r"https?://arxiv\.org/abs/([A-Za-z0-9_.-]+)")
ARXIV_PDF_RE = re.compile(r"https?://arxiv\.org/pdf/([A-Za-z0-9_.-]+)(?:\.pdf)?")
PDF_URL_RE = re.compile(r"https?://[^\s)>\"]+\.pdf(?:\?[^\s)>\"]*)?", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s)>\"]+")
LOCAL_SNIPPET_CHARS = 1200
WEB_SNIPPET_CHARS = 4000


def _paper_dir(paper_id: str) -> Path:
    return RESEARCH_PAPERS_DIR / paper_id


def _read_text_file(path: Path, max_chars: int = LOCAL_SNIPPET_CHARS) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception:
        return ""


def list_article_candidates(query: str, *, limit: int = 12) -> list[dict]:
    ensure_using_dirs()
    query_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    candidates = []
    for path in ARTICLE_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in ARTICLE_EXTENSIONS:
            continue
        name_l = path.name.lower()
        stem_l = path.stem.lower()
        name_tokens = set(re.findall(r"[a-z0-9]+", stem_l))
        overlap = query_tokens & name_tokens
        score = len(overlap) * 3
        if query.lower() in name_l or stem_l in query.lower():
            score += 8
        if any(token in name_l for token in query_tokens):
            score += 2
        candidates.append({
            "paper_id": paper_id_from_name(path.name),
            "article_path": str(path),
            "article_name": path.name,
            "suffix": path.suffix.lower(),
            "score": score,
            "snippet": "" if path.suffix.lower() == ".pdf" else _read_text_file(path),
        })
    candidates.sort(key=lambda item: (-item["score"], item["article_name"]))
    return candidates[:limit]


def _choose_candidate_with_llm(query: str, candidates: list[dict], engine=None) -> dict | None:
    if not candidates:
        return None
    if engine is None:
        best = candidates[0]
        return best if best.get("score", 0) > 0 or len(candidates) == 1 else None

    manifest = []
    for idx, item in enumerate(candidates, start=1):
        manifest.append({
            "index": idx,
            "paper_id": item.get("paper_id"),
            "article_name": item.get("article_name") or item.get("title") or item.get("url"),
            "url": item.get("url"),
            "snippet": (item.get("snippet") or item.get("text") or "")[:700],
        })

    prompt = (
        "你要从候选论文中选择最适合用户复现需求的一篇。"
        "如果没有明确相关论文，返回 {\"selected_index\": 0, \"reason\": \"...\"}。"
        "只输出 JSON，不要输出其他文字。\n\n"
        f"用户需求：{query}\n\n候选：\n"
        f"{json.dumps(manifest, ensure_ascii=False, indent=2)}"
    )
    try:
        response = engine._client.chat.completions.create(
            model=engine.model,
            max_tokens=300,
            messages=[
                {"role": "system", "content": "你是论文检索助手，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
        )
        raw = response.choices[0].message.content or ""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group())
        selected_index = int(data.get("selected_index", 0))
        if selected_index <= 0 or selected_index > len(candidates):
            return None
        selected = dict(candidates[selected_index - 1])
        selected["selection_reason"] = data.get("reason", "")
        return selected
    except Exception:
        best = candidates[0]
        return best if best.get("score", 0) > 0 or len(candidates) == 1 else None


def _fetch_url(url: str, *, timeout: int = 25) -> tuple[str, str]:
    from ..tools.web import _fetch_url as web_fetch_url

    status, content_type, body = web_fetch_url(url, timeout=timeout)
    if status == 0 or status >= 400:
        raise RuntimeError(body or f"HTTP status {status}")
    return content_type, body


def _html_to_text(html: str) -> str:
    try:
        from ..tools.web import _TextExtractor

        parser = _TextExtractor()
        parser.feed(html)
        return parser.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def _search_web_duckduckgo(query: str, *, limit: int = 8) -> list[dict]:
    search_url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        _, html = _fetch_url(search_url)
    except Exception as exc:
        return [{"ok": False, "source": "duckduckgo", "error": str(exc)}]

    results = []
    for match in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
        href = match.group(1)
        title = re.sub(r"\s+", " ", _html_to_text(match.group(2))).strip()
        if "uddg=" in href:
            parsed = urllib.parse.urlparse(href)
            params = urllib.parse.parse_qs(parsed.query)
            href = params.get("uddg", [href])[0]
        results.append({"title": title, "url": href, "snippet": ""})
        if len(results) >= limit:
            break

    if not results:
        text = _html_to_text(html)
        for url in URL_RE.findall(text):
            results.append({"title": url, "url": url, "snippet": ""})
            if len(results) >= limit:
                break
    return results


def _search_web_arxiv(query: str, *, limit: int = 8) -> list[dict]:
    api_url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
    })
    try:
        _, xml = _fetch_url(api_url)
    except Exception as exc:
        return [{"ok": False, "source": "arxiv", "error": str(exc)}]

    results = []
    for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.DOTALL):
        title_match = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
        summary_match = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
        id_match = re.search(r"<id>(.*?)</id>", entry, re.DOTALL)
        if not id_match:
            continue
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else id_match.group(1)
        summary = re.sub(r"\s+", " ", summary_match.group(1)).strip() if summary_match else ""
        abs_url = id_match.group(1).strip()
        results.append({
            "title": title,
            "url": abs_url,
            "pdf_url": abs_url.replace("/abs/", "/pdf/") + ".pdf" if "/abs/" in abs_url else abs_url,
            "snippet": summary[:WEB_SNIPPET_CHARS],
            "source": "arxiv",
        })
    return results


def find_web_paper_candidates(query: str, *, limit: int = 8) -> list[dict]:
    seen: set[str] = set()
    candidates = []
    for item in _search_web_arxiv(query, limit=limit):
        url = item.get("url")
        if url and url not in seen:
            seen.add(url)
            candidates.append(item)
    for item in _search_web_duckduckgo(f"{query} paper pdf github", limit=limit):
        url = item.get("url")
        if url and url not in seen:
            seen.add(url)
            candidates.append(item)
    return candidates[:limit]


def _download_binary(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (shell-agent/1.0)"})
    with urllib.request.urlopen(req, timeout=60) as response:
        dest.write_bytes(response.read())


def _materialize_web_candidate(candidate: dict) -> dict:
    ensure_using_dirs()
    url = candidate.get("pdf_url") or candidate.get("url") or ""
    if not url:
        return {"ok": False, "error": "Selected web candidate has no URL.", "candidate": candidate}

    abs_match = ARXIV_ABS_RE.search(url)
    pdf_match = ARXIV_PDF_RE.search(url)
    if abs_match:
        arxiv_id = abs_match.group(1)
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        filename = f"{arxiv_id}.pdf"
    elif pdf_match:
        arxiv_id = pdf_match.group(1)
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        filename = f"{arxiv_id}.pdf"
    else:
        parsed_name = Path(urllib.parse.urlparse(url).path).name
        filename = parsed_name if parsed_name else paper_id_from_name(candidate.get("title") or url)
        if not Path(filename).suffix:
            filename += ".txt"

    dest = ARTICLE_DIR / filename
    try:
        if url.lower().split("?")[0].endswith(".pdf"):
            _download_binary(url, dest)
            return {"ok": True, "article_path": str(dest), "article_name": dest.name, "url": url}

        content_type, body = _fetch_url(url, timeout=45)
        text = _html_to_text(body) if "html" in content_type.lower() else body
        text_dest = dest if dest.suffix.lower() in {".txt", ".md"} else dest.with_suffix(".txt")
        text_dest.write_text(text, encoding="utf-8", errors="replace")
        return {"ok": True, "article_path": str(text_dest), "article_name": text_dest.name, "url": url}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url, "candidate": candidate}


def _search_web_code_candidates(query: str) -> tuple[list[str], list[dict]]:
    urls = []
    errors = []
    for item in _search_web_duckduckgo(f"{query} GitHub GitLab implementation source code", limit=10):
        if item.get("ok") is False:
            errors.append(item)
            continue
        blob = " ".join(str(item.get(k, "")) for k in ("title", "url", "snippet"))
        urls.extend(_normalize_git_url(url) for url in GIT_RE.findall(blob))
    return sorted(set(urls), key=lambda url: (-_candidate_score(url), url)), errors


def _web_search_code_candidates(query: str) -> list[str]:
    urls, _ = _search_web_code_candidates(query)
    return urls


def _candidate_score(url: str) -> int:
    lowered = url.lower()
    score = 0
    for word in ("official", "author", "hnsw", "paper", "implementation"):
        if word in lowered:
            score += 1
    return score


def _normalize_github_url(url: str) -> str:
    return _normalize_git_url(url)


def _normalize_git_url(url: str) -> str:
    url = url.rstrip(".,;:)")
    return url if url.startswith(("http://", "https://")) else f"https://{url}"


def find_code_candidates(query_or_paper_id: str) -> dict:
    info = ensure_paper_chunks(query_or_paper_id)
    if not info.get("ok"):
        return info

    paper_id = info["paper_id"]
    chunks = read_chunks(paper_id)
    text = chunks_text(chunks)
    candidates = sorted(
        {_normalize_git_url(url) for url in GIT_RE.findall(text)},
        key=lambda u: (-_candidate_score(u), u),
    )

    paper_dir = _paper_dir(paper_id)
    paper_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "ok": True,
        "paper_id": paper_id,
        "candidates": [{"url": url, "score": _candidate_score(url)} for url in candidates],
        "candidate_count": len(candidates),
    }
    (paper_dir / "code_candidates.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def _repo_dir_name(url: str) -> str:
    path = urlparse(url).path.strip("/")
    name = path.split("/")[-1] if path else "repo"
    return name[:-4] if name.endswith(".git") else name


def _clone_repo(url: str, target: Path) -> dict:
    if target.exists():
        return {"cloned": False, "repo_dir": str(target), "message": "Repository directory already exists."}
    result = subprocess.run(
        ["git", "clone", url, str(target)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {
        "cloned": result.returncode == 0,
        "repo_dir": str(target),
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def _write_repro_plan(paper_id: str, repo_url: str | None, repo_dir: Path | None) -> Path:
    paper_dir = _paper_dir(paper_id)
    runs_dir = paper_dir / "runs" / "latest"
    runs_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runs_dir / "reproduction_plan.md"
    env_name = f"repro-{paper_id[:24]}"
    lines = [
        f"# Reproduction Plan: {paper_id}",
        "",
        "This file was generated by the reconstructed research workflow.",
        "",
    ]
    if not repo_url:
        lines.extend(
            [
                "No Git repository URL was found in the paper chunks.",
                "Tell the user that the paper may not have an obvious open-source implementation.",
            ]
        )
    else:
        lines.extend(
            [
                f"Repository: {repo_url}",
                f"Local code directory: {repo_dir}",
                "",
                "Suggested commands:",
                "```bash",
                f"conda create -n {env_name} python=3.10 -y",
                f"conda activate {env_name}",
                f"cd {repo_dir}",
                "# Inspect README / requirements before running arbitrary code.",
                "[ -f requirements.txt ] && pip install -r requirements.txt",
                "# Run the documented entry point from the repository README.",
                "```",
            ]
        )
    plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plan_path


def prepare_reproduction_from_paper(query_or_paper_id: str, *, clone: bool = True) -> dict:
    info = ensure_paper_chunks(query_or_paper_id)
    if not info.get("ok"):
        return info

    paper_id = info["paper_id"]
    candidates = find_code_candidates(paper_id)
    urls = [item["url"] for item in candidates.get("candidates", [])]
    repo_url = urls[0] if urls else None

    repo_result = None
    repo_dir = None
    if repo_url:
        repo_dir = _paper_dir(paper_id) / "code" / _repo_dir_name(repo_url)
        repo_result = _clone_repo(repo_url, repo_dir) if clone else {"cloned": False, "repo_dir": str(repo_dir)}

    plan_path = _write_repro_plan(paper_id, repo_url, repo_dir)
    result = {
        "ok": True,
        "paper_id": paper_id,
        "chunk_count": len(read_chunks(paper_id)),
        "code_candidates_path": str(_paper_dir(paper_id) / "code_candidates.json"),
        "repo_url": repo_url,
        "repo_result": repo_result,
        "plan_path": str(plan_path),
    }
    ResearchLibrary().upsert_paper(paper_id, result)
    return result


def prepare_reproduction_from_request(
    request: str,
    *,
    clone: bool = True,
    allow_web: bool = True,
    engine=None,
) -> dict:
    local_candidates = list_article_candidates(request)
    selected = _choose_candidate_with_llm(request, local_candidates, engine)
    source = "local_article"
    web_candidates: list[dict] = []
    materialized = None

    if selected is None and allow_web:
        web_candidates = find_web_paper_candidates(request)
        usable_web = [item for item in web_candidates if item.get("url")]
        selected = _choose_candidate_with_llm(request, usable_web, engine)
        source = "web"
        if selected is not None:
            materialized = _materialize_web_candidate(selected)
            if not materialized.get("ok"):
                return {
                    "ok": False,
                    "stage": "fetch_web_paper",
                    "request": request,
                    "local_candidates": local_candidates,
                    "web_candidates": web_candidates,
                    "selected": selected,
                    "error": materialized.get("error", "Failed to fetch selected web paper."),
                    "detail": materialized,
                }
            selected = {
                **selected,
                "article_path": materialized["article_path"],
                "article_name": materialized["article_name"],
            }

    if selected is None:
        web_errors = [item for item in web_candidates if item.get("ok") is False]
        return {
            "ok": False,
            "stage": "select_paper",
            "request": request,
            "error": "No relevant paper found under using/article and web search was disabled or did not find a usable paper.",
            "local_candidates": local_candidates,
            "web_candidates": web_candidates,
            "web_errors": web_errors,
            "next_step": "If web_errors indicate network or anti-bot failure, use web_fetch or browser_navigate to search manually, then call research_fetch_paper or research_prepare_from_paper.",
        }

    query = selected.get("article_name") or selected.get("paper_id") or request
    result = prepare_reproduction_from_paper(query, clone=clone)
    result.update({
        "request": request,
        "paper_source": source,
        "selected_paper": selected,
        "local_candidates": local_candidates,
    })
    if web_candidates:
        result["web_candidates"] = web_candidates
    if materialized:
        result["materialized_paper"] = materialized

    if result.get("ok") and not result.get("repo_url"):
        web_code_errors: list[dict] = []
        if allow_web:
            web_code_urls, web_code_errors = _search_web_code_candidates(request)
        else:
            web_code_urls = []
        if web_code_urls:
            repo_url = web_code_urls[0]
            paper_id = result["paper_id"]
            repo_dir = _paper_dir(paper_id) / "code" / _repo_dir_name(repo_url)
            repo_result = _clone_repo(repo_url, repo_dir) if clone else {"cloned": False, "repo_dir": str(repo_dir)}
            plan_path = _write_repro_plan(paper_id, repo_url, repo_dir)
            result.update({
                "repo_url": repo_url,
                "repo_result": repo_result,
                "repo_source": "web_search",
                "web_code_candidates": [{"url": url, "score": _candidate_score(url)} for url in web_code_urls],
                "plan_path": str(plan_path),
            })
            ResearchLibrary().upsert_paper(paper_id, result)
        else:
            result.update({
                "ok": False,
                "stage": "find_code",
                "no_repository_found": True,
                "error": "No Git repository URL found in paper chunks or web search results.",
                "web_code_candidates": [],
                "web_errors": web_code_errors,
                "next_step": "If network or anti-bot errors are present, use web_fetch or browser_navigate to search for an implementation manually. If that still finds no Git repository URL, report that no open code repository was found.",
            })

    return result


def research_prepare_from_paper(query_or_paper_id: str, *, clone: bool = True) -> dict:
    return prepare_reproduction_from_paper(query_or_paper_id, clone=clone)
