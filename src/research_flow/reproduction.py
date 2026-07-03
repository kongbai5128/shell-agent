from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ..paths import ARTICLE_DIR, RESEARCH_PAPERS_DIR, ensure_using_dirs
from .library import ResearchLibrary, paper_id_from_name
from .papers import ARTICLE_EXTENSIONS, ensure_paper_text, read_extracted_text


GIT_RE = re.compile(r"(?:https?://)?(?:github\.com|gitlab\.com|bitbucket\.org)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?")
ARXIV_ABS_RE = re.compile(r"https?://arxiv\.org/abs/([A-Za-z0-9_.-]+)")
ARXIV_PDF_RE = re.compile(r"https?://arxiv\.org/pdf/([A-Za-z0-9_.-]+)(?:\.pdf)?")
URL_RE = re.compile(r"https?://[^\s)>\"]+")
LOCAL_SNIPPET_CHARS = 1200
WEB_SNIPPET_CHARS = 4000


def _paper_dir(paper_id: str) -> Path:
    """返回论文在 using/research/papers 下的专属目录。"""
    return RESEARCH_PAPERS_DIR / paper_id


def _read_text_file(path: Path, max_chars: int = LOCAL_SNIPPET_CHARS) -> str:
    """读取 TXT/MD 论文的短预览，用于本地候选排序。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception:
        return ""


def list_article_candidates(query: str, *, limit: int = 12) -> list[dict]:
    """根据自然语言复现需求，对本地论文候选进行排序。"""
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
    """让当前模型从候选论文中选择最相关的一篇。"""
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
    """复用现有 web 工具实现抓取 URL。"""
    from ..tools.web import _fetch_url as web_fetch_url

    status, content_type, body = web_fetch_url(url, timeout=timeout)
    if status == 0 or status >= 400:
        raise RuntimeError(body or f"HTTP status {status}")
    return content_type, body


def _html_to_text(html: str) -> str:
    """将 HTML 搜索结果转成纯文本，便于兜底解析。"""
    try:
        from ..tools.web import _TextExtractor

        parser = _TextExtractor()
        parser.feed(html)
        return parser.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def _search_web_duckduckgo(query: str, *, limit: int = 8) -> list[dict]:
    """搜索 DuckDuckGo HTML 结果，返回轻量 URL 候选。"""
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
    """搜索 arXiv，返回带 abs/pdf 链接的论文候选。"""
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
    """本地没有合适论文时，合并 arXiv 和通用网页搜索结果。"""
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
    """下载 PDF 等二进制论文文件到 using/article。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (shell-agent/1.0)"})
    with urllib.request.urlopen(req, timeout=60) as response:
        dest.write_bytes(response.read())


def _materialize_web_candidate(candidate: dict) -> dict:
    """将选中的网络论文保存到 using/article，供后续正文抽取。"""
    ensure_using_dirs()
    url = candidate.get("pdf_url") or candidate.get("url") or ""
    if not url:
        return {"ok": False, "error": "选中的网络论文候选没有 URL。", "candidate": candidate}

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


def _candidate_score(url: str) -> int:
    """根据 URL 特征给仓库候选打轻量分数。"""
    lowered = url.lower()
    score = 0
    for word in ("official", "author", "hnsw", "paper", "implementation"):
        if word in lowered:
            score += 1
    return score


def _normalize_github_url(url: str) -> str:
    """兼容旧的 GitHub-only 调用。"""
    return _normalize_git_url(url)


def _normalize_git_url(url: str) -> str:
    """补齐仓库 URL 协议头，并去掉末尾标点。"""
    url = url.rstrip(".,;:)")
    return url if url.startswith(("http://", "https://")) else f"https://{url}"


def _choose_repo_with_llm(request: str, paper_id: str, candidates: list[dict], engine=None) -> dict | None:
    """从仓库候选中选择最合适的一个，不写候选 JSON 文件。"""
    if not candidates:
        return None
    if len(candidates) == 1 or engine is None:
        return candidates[0]

    prompt = (
        "你要从论文正文中提取到的代码仓库候选里选择最适合复现用户需求的一个。"
        "如果没有明显合适的仓库，返回 {\"selected_index\": 0, \"reason\": \"...\"}。"
        "只输出 JSON。\n\n"
        f"用户需求：{request}\n论文 ID：{paper_id}\n候选仓库：\n"
        f"{json.dumps([{**item, 'index': idx} for idx, item in enumerate(candidates, start=1)], ensure_ascii=False, indent=2)}"
    )
    try:
        response = engine._client.chat.completions.create(
            model=engine.model,
            max_tokens=300,
            messages=[
                {"role": "system", "content": "你是论文复现代码仓库选择助手，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
        )
        raw = response.choices[0].message.content or ""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return candidates[0]
        data = json.loads(match.group())
        selected_index = int(data.get("selected_index", 0))
        if selected_index <= 0 or selected_index > len(candidates):
            return None
        selected = dict(candidates[selected_index - 1])
        selected["selection_reason"] = data.get("reason", "")
        return selected
    except Exception:
        return candidates[0]


def find_code_candidates(query_or_paper_id: str) -> dict:
    """从论文正文中提取 Git 仓库候选；只返回 URL 候选，不返回全文。"""
    info = ensure_paper_text(query_or_paper_id)
    if not info.get("ok"):
        return info

    paper_id = info["paper_id"]
    text = read_extracted_text(paper_id)
    candidates = sorted(
        {_normalize_git_url(url) for url in GIT_RE.findall(text)},
        key=lambda u: (-_candidate_score(u), u),
    )

    return {
        "ok": True,
        "paper_id": paper_id,
        "candidates": [{"url": url, "score": _candidate_score(url)} for url in candidates],
        "candidate_count": len(candidates),
        "text_chars": len(text),
    }


def _repo_dir_name(url: str) -> str:
    """根据仓库 URL 推导稳定的本地 clone 目录名。"""
    path = urlparse(url).path.strip("/")
    name = path.split("/")[-1] if path else "repo"
    return name[:-4] if name.endswith(".git") else name


def _clone_repo(url: str, target: Path) -> dict:
    """克隆选中的仓库；若目标目录已存在则跳过。"""
    if target.exists():
        return {"cloned": False, "repo_dir": str(target), "message": "仓库目录已存在，跳过 clone。"}
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


def _quote_path(path: Path) -> str:
    """把本地路径转成可直接放进 shell 命令的形式。"""
    return shlex.quote(str(path))


def _relative_command_path(path: Path, base: Path) -> str:
    """返回相对仓库根目录的 shell 路径。"""
    try:
        return shlex.quote(str(path.relative_to(base)))
    except ValueError:
        return _quote_path(path)


def _build_run_steps(paper_id: str, repo_url: str | None, repo_dir: Path | None) -> dict:
    """根据仓库文件生成用户后续可复制执行的运行步骤。"""
    env_name = f"repro-{paper_id[:24]}"
    steps = {
        "conda_env": env_name,
        "repo_url": repo_url,
        "repo_dir": str(repo_dir) if repo_dir else None,
        "setup_commands": [],
        "install_commands": [],
        "build_commands": [],
        "verification_commands": [],
        "run_commands": [],
        "resume_commands": [],
        "notes": [],
    }

    if not repo_url or repo_dir is None:
        steps["notes"].append("论文正文中没有找到 Git 仓库 URL，无法生成仓库启动命令。")
        steps["commands"] = []
        steps["resume_commands"] = []
        return steps

    steps["setup_commands"] = [
        f"conda create -n {env_name} python=3.10 -y",
        f"conda activate {env_name}",
        f"cd {_quote_path(repo_dir)}",
    ]

    if not repo_dir.exists():
        steps["notes"].append("代码目录尚不存在；clone 完成后再进入该目录执行安装和启动命令。")
        steps["commands"] = steps["setup_commands"]
        steps["resume_commands"] = [
            f"conda activate {env_name}",
            f"cd {_quote_path(repo_dir)}",
        ]
        return steps

    env_file = next((name for name in ("environment.yml", "environment.yaml") if (repo_dir / name).exists()), None)
    if env_file:
        steps["install_commands"].append(f"conda env update -n {env_name} -f {shlex.quote(env_file)}")

    if (repo_dir / "requirements.txt").exists():
        steps["install_commands"].append("pip install -r requirements.txt")
    if (repo_dir / "pyproject.toml").exists() or (repo_dir / "setup.py").exists():
        steps["install_commands"].append("pip install -e .")
    if not steps["install_commands"]:
        steps["install_commands"].append("# 未检测到 requirements.txt / pyproject.toml / setup.py，请先查看 README。")

    if (repo_dir / "CMakeLists.txt").exists():
        steps["build_commands"].extend([
            "cmake -S . -B build",
            "cmake --build build -j$(nproc)",
        ])

    tests_python = repo_dir / "tests" / "python"
    tests_dir = repo_dir / "tests"
    if tests_python.exists():
        steps["verification_commands"].append(
            'python -m unittest discover --start-directory tests/python --pattern "bindings_test*.py"'
        )
    elif tests_dir.exists():
        steps["verification_commands"].append("python -m unittest discover -s tests")

    examples_python = repo_dir / "examples" / "python"
    if (examples_python / "example.py").exists():
        steps["run_commands"].append("python examples/python/example.py")
    elif examples_python.exists():
        example = next(iter(sorted(examples_python.glob("*.py"))), None)
        if example:
            steps["run_commands"].append(f"python {_relative_command_path(example, repo_dir)}")
    elif (repo_dir / "main.py").exists():
        steps["run_commands"].append("python main.py")
    elif (repo_dir / "app.py").exists():
        steps["run_commands"].append("python app.py")
    else:
        steps["run_commands"].append("# 按 README 中记录的入口命令启动复现。")

    steps["commands"] = (
        steps["setup_commands"]
        + steps["install_commands"]
        + steps["build_commands"]
        + steps["verification_commands"]
        + steps["run_commands"]
    )
    steps["resume_commands"] = [
        f"conda activate {env_name}",
        f"cd {_quote_path(repo_dir)}",
        *steps["run_commands"],
    ]
    return steps


def _find_conda_executable() -> str | None:
    """寻找可用的 conda 可执行文件。"""
    candidates = [
        os.environ.get("CONDA_EXE"),
        shutil.which("conda"),
        "/home/qian/miniconda3/bin/conda",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _run_setup_command(command: list[str], *, cwd: Path | None = None, timeout: int = 1800) -> dict:
    """执行环境安装命令，并返回简洁的执行结果。"""
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "ok": result.returncode == 0,
        "command": " ".join(shlex.quote(part) for part in command),
        "cwd": str(cwd) if cwd else None,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def _conda_env_exists(conda: str, env_name: str) -> bool:
    """检查指定 conda 环境是否已经存在。"""
    result = subprocess.run(
        [conda, "env", "list", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return False
    try:
        envs = json.loads(result.stdout).get("envs", [])
    except Exception:
        return False
    return any(Path(env).name == env_name for env in envs)


def _setup_repro_environment(run_steps: dict) -> dict:
    """真正创建 conda 环境并安装仓库依赖。"""
    env_name = run_steps.get("conda_env")
    repo_dir_raw = run_steps.get("repo_dir")
    if not env_name or not repo_dir_raw:
        return {"ok": False, "executed": False, "error": "缺少 conda 环境名或代码目录。", "commands": []}

    repo_dir = Path(repo_dir_raw)
    if not repo_dir.exists():
        return {"ok": False, "executed": False, "error": "代码目录不存在，无法安装环境。", "commands": []}

    conda = _find_conda_executable()
    if conda is None:
        return {"ok": False, "executed": False, "error": "没有找到 conda 可执行文件。", "commands": []}

    results = []
    env_existed = _conda_env_exists(conda, env_name)
    if not env_existed:
        results.append(_run_setup_command([conda, "create", "-n", env_name, "python=3.10", "-y"], timeout=1800))
        if not results[-1]["ok"]:
            return {
                "ok": False,
                "executed": True,
                "conda": conda,
                "conda_env": env_name,
                "env_existed": False,
                "commands": results,
                "error": "创建 conda 环境失败。",
            }

    results.append(_run_setup_command([conda, "run", "-n", env_name, "python", "-m", "pip", "install", "--upgrade", "pip"], cwd=repo_dir))

    env_file = next((name for name in ("environment.yml", "environment.yaml") if (repo_dir / name).exists()), None)
    if env_file:
        results.append(_run_setup_command([conda, "env", "update", "-n", env_name, "-f", env_file], cwd=repo_dir))

    if (repo_dir / "requirements.txt").exists():
        results.append(_run_setup_command([conda, "run", "-n", env_name, "python", "-m", "pip", "install", "-r", "requirements.txt"], cwd=repo_dir))

    if (repo_dir / "pyproject.toml").exists() or (repo_dir / "setup.py").exists():
        results.append(_run_setup_command([conda, "run", "-n", env_name, "python", "-m", "pip", "install", "-e", "."], cwd=repo_dir))

    failed = [item for item in results if not item.get("ok")]
    return {
        "ok": not failed,
        "executed": True,
        "conda": conda,
        "conda_env": env_name,
        "env_existed": env_existed,
        "commands": results,
        "error": failed[0]["stderr"] if failed else "",
    }


def _write_repro_plan(
    paper_id: str,
    repo_url: str | None,
    repo_dir: Path | None,
    run_steps: dict,
    setup_result: dict | None = None,
) -> Path:
    """写入最终复现计划；这是需要持久化的输出产物。"""
    paper_dir = _paper_dir(paper_id)
    runs_dir = paper_dir / "runs" / "latest"
    runs_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runs_dir / "reproduction_plan.md"
    lines = [
        f"# 复现计划：{paper_id}",
        "",
        "由恢复后的 research 流程生成。",
        "",
    ]
    if not repo_url:
        lines.extend(
            [
                "论文正文中没有找到 Git 仓库 URL。",
                "可向用户说明：该论文可能没有明显的开源实现。",
            ]
        )
    else:
        lines.extend(
            [
                f"仓库：{repo_url}",
                f"本地代码目录：{repo_dir}",
                f"Conda 环境：{run_steps.get('conda_env')}",
                "",
                "## 环境安装状态",
                "",
                f"- 是否已执行安装：{bool(setup_result and setup_result.get('executed'))}",
                f"- 安装是否成功：{bool(setup_result and setup_result.get('ok'))}",
                "",
                "## 后续启动步骤",
                "",
                "环境和依赖已经准备好后，后续可以用下面命令重新启动：",
                "",
                "```bash",
                *run_steps.get("resume_commands", []),
                "```",
                "",
                "## 首次配置 / 验证步骤",
                "",
                "如果是第一次复现，按下面命令创建环境、安装依赖并验证：",
                "",
                "```bash",
                *run_steps.get("commands", []),
                "```",
            ]
        )
        notes = run_steps.get("notes") or []
        if notes:
            lines.extend(["", "## 注意事项", "", *[f"- {note}" for note in notes]])
        if setup_result and setup_result.get("commands"):
            lines.extend(["", "## 实际执行的安装命令", ""])
            for item in setup_result["commands"]:
                status = "成功" if item.get("ok") else "失败"
                lines.extend([f"- {status}: `{item.get('command')}`"])
    plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plan_path


def prepare_reproduction_from_paper(
    query_or_paper_id: str,
    *,
    clone: bool = True,
    setup_env: bool = True,
    request: str = "",
    engine=None,
) -> dict:
    """基于已知论文 ID 或本地论文文件名准备复现输出。"""
    candidates = find_code_candidates(query_or_paper_id)
    if not candidates.get("ok"):
        return candidates

    paper_id = candidates["paper_id"]
    repo_candidates = candidates.get("candidates", [])
    selected_repo = _choose_repo_with_llm(request or query_or_paper_id, paper_id, repo_candidates, engine)
    repo_url = selected_repo.get("url") if selected_repo else None

    repo_result = None
    repo_dir = None
    if repo_url:
        repo_dir = _paper_dir(paper_id) / "code" / _repo_dir_name(repo_url)
        repo_result = _clone_repo(repo_url, repo_dir) if clone else {"cloned": False, "repo_dir": str(repo_dir)}

    run_steps = _build_run_steps(paper_id, repo_url, repo_dir)
    setup_result = _setup_repro_environment(run_steps) if repo_url and setup_env else {
        "ok": False,
        "executed": False,
        "message": "本次只生成复现计划，未安装 conda 环境。",
    }
    run_steps["environment_ready"] = bool(setup_result.get("ok"))
    plan_path = _write_repro_plan(paper_id, repo_url, repo_dir, run_steps, setup_result)
    result = {
        "ok": bool(repo_url) and (not setup_env or bool(setup_result.get("ok"))),
        "paper_id": paper_id,
        "text_chars": candidates.get("text_chars", 0),
        "code_candidates": repo_candidates,
        "selected_repo": selected_repo,
        "repo_url": repo_url,
        "repo_result": repo_result,
        "run_steps": run_steps,
        "setup_result": setup_result,
        "plan_path": str(plan_path),
    }
    if not repo_url:
        result.update({
            "stage": "find_code",
            "no_repository_found": True,
            "error": "论文正文中没有找到 Git 仓库 URL，可判断该论文没有明确开源仓库。",
        })
    elif setup_env and not setup_result.get("ok"):
        result.update({
            "stage": "setup_environment",
            "error": setup_result.get("error") or "环境安装失败。",
        })
    ResearchLibrary().upsert_paper(paper_id, result)
    return result


def prepare_reproduction_from_request(
    request: str,
    *,
    clone: bool = True,
    setup_env: bool = True,
    allow_web: bool = True,
    engine=None,
) -> dict:
    """执行从自然语言复现需求到仓库选择/clone 的完整流程。"""
    # 1. 文章候选：先扫描 using/article 下的本地论文。
    local_candidates = list_article_candidates(request)
    selected = _choose_candidate_with_llm(request, local_candidates, engine)
    source = "local_article"
    web_candidates: list[dict] = []
    materialized = None

    # 2. 文章候选兜底：本地没有合适论文时，再联网搜索并下载论文。
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
                    "error": materialized.get("error", "获取选中的网络论文失败。"),
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
            "error": "using/article 下没有找到相关论文，且联网搜索被禁用或没有找到可用论文。",
            "local_candidates": local_candidates,
            "web_candidates": web_candidates,
            "web_errors": web_errors,
            "next_step": "如果 web_errors 显示网络或反爬限制，请用 web_fetch 或 browser_navigate 手动搜索，再调用 research_fetch_paper 或 research_prepare_from_paper。",
        }

    # 3. Git 仓库候选：只从选中论文正文中本地提取 URL，再选择最合适的仓库。
    query = selected.get("article_name") or selected.get("paper_id") or request
    result = prepare_reproduction_from_paper(
        query,
        clone=clone,
        setup_env=setup_env,
        request=request,
        engine=engine,
    )
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

    return result


def research_prepare_from_paper(query_or_paper_id: str, *, clone: bool = True, setup_env: bool = True) -> dict:
    """兼容旧接口：包装 prepare_reproduction_from_paper。"""
    return prepare_reproduction_from_paper(query_or_paper_id, clone=clone, setup_env=setup_env)
