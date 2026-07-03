from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
USING_DIR = PROJECT_ROOT / "using"
ARTICLE_DIR = USING_DIR / "article"
RESEARCH_DIR = USING_DIR / "research"
RESEARCH_PAPERS_DIR = RESEARCH_DIR / "papers"
MEMORY_DIR = USING_DIR / "memory"
MEMORY_INDEX_FILE = MEMORY_DIR / "_index.json"
SESSIONS_DIR = USING_DIR / "sessions"
SKILLS_DIR = USING_DIR / "skills"
AGENTS_DIR = USING_DIR / "agents"
FILE_HISTORY_DIR = USING_DIR / "file_history"
TODO_FILE = USING_DIR / "todos.json"
MCP_PROJECT_CONFIG = USING_DIR / "mcp.json"
DREAM_LOG = MEMORY_DIR / "dream.log"


def ensure_using_dirs() -> None:
    """创建 shell-agent 运行时需要持久化的 using 子目录。"""
    for path in (
        ARTICLE_DIR,
        RESEARCH_PAPERS_DIR,
        MEMORY_DIR,
        SESSIONS_DIR,
        SKILLS_DIR,
        AGENTS_DIR,
        FILE_HISTORY_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
