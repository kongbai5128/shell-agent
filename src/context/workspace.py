from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


AGENT_MD_NAMES = ["AGENT.md", "CLAUDE.md", "CLAW.md", ".agent-instructions.md"]


@dataclass(frozen=True)
class WorkspaceContext:
    """当前工作目录的项目上下文，借鉴自 claw-code src/context.py"""
    cwd: Path
    agent_md_path: Path | None
    agent_md_content: str | None
    git_root: Path | None
    python_files: int
    has_tests: bool

    def system_prompt_block(self) -> str:
        """生成注入到 system prompt 的工作区说明"""
        lines = [f"当前工作目录: {self.cwd}"]
        if self.git_root:
            lines.append(f"Git 根目录: {self.git_root}")
        lines.append(f"Python 文件数: {self.python_files}")
        lines.append(f"有测试目录: {'是' if self.has_tests else '否'}")
        if self.agent_md_content:
            lines.append(f"\n--- {self.agent_md_path.name} ---")
            lines.append(self.agent_md_content.strip())
            lines.append("--- end ---")
        return "\n".join(lines)


def build_workspace_context(cwd: Path | None = None) -> WorkspaceContext:
    root = cwd or Path.cwd()

    # 查找 AGENT.md / CLAUDE.md 等
    agent_md_path = None
    agent_md_content = None
    for name in AGENT_MD_NAMES:
        candidate = root / name
        if candidate.exists():
            agent_md_path = candidate
            agent_md_content = candidate.read_text(encoding="utf-8")
            break

    # 查找 git root
    git_root = None
    check = root
    for _ in range(6):
        if (check / ".git").exists():
            git_root = check
            break
        check = check.parent

    python_files = sum(1 for _ in root.rglob("*.py") if _.is_file())
    has_tests = (root / "tests").is_dir() or (root / "test").is_dir()

    return WorkspaceContext(
        cwd=root,
        agent_md_path=agent_md_path,
        agent_md_content=agent_md_content,
        git_root=git_root,
        python_files=python_files,
        has_tests=has_tests,
    )
