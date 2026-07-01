"""
Skill 系统 — 对标 claw-code skills/loadSkillsDir.ts + bundledSkills.ts

Skill 是存储在文件里的可复用 prompt 片段，Claude 调用 skill 工具时
会读取对应的 .md 文件注入到对话中，相当于"预定义的专家角色"。

目录结构：
  using/skills/                ← 用户自定义 skill
  src/skills/bundled/          ← 内置 skill
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..paths import SKILLS_DIR

# ── Skill 存储路径 ─────────────────────────────────────────────
BUNDLED_SKILLS_DIR = Path(__file__).parent / "bundled"
USER_SKILLS_DIR = SKILLS_DIR


@dataclass(frozen=True)
class Skill:
    name: str           # skill 名称（文件名不含扩展）
    path: Path          # 文件路径
    description: str    # 从文件第一行提取的简介
    when_to_use: str    # 新增：何时使用这个 skill（从 ## When to use 提取）
    prompt: str         # 完整内容

def _parse_frontmatter(content: str) -> dict:
    """解析 YAML frontmatter（--- ... --- 块），返回字段字典"""
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = content[3:end].strip()
    result: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip()
    return result


def _parse_when_to_use(content: str) -> str:
    """优先从 YAML frontmatter 读取 when_to_use，再回退到 ## When to use 段落"""
    # 1. YAML frontmatter
    fm = _parse_frontmatter(content)
    if fm.get("when_to_use"):
        return fm["when_to_use"][:200]
    # 2. Markdown 段落
    match = re.search(
        r'##\s*(?:When to use|何时使用)[^\n]*\n(.*?)(?=\n##|\Z)',
        content, re.IGNORECASE | re.DOTALL
    )
    if match:
        return match.group(1).strip()[:200]
    return ""


def _parse_description(content: str) -> str:
    """优先从 YAML frontmatter 读取 description，再回退到第一行非空文字"""
    # 1. YAML frontmatter
    fm = _parse_frontmatter(content)
    if fm.get("description"):
        return fm["description"][:120]
    # 2. 跳过 frontmatter 块后取第一行标题
    body = content
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            body = content[end + 4:]
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:120]
    return "(无简介)"


def load_skill(name: str) -> Skill | None:
    for skills_dir in (USER_SKILLS_DIR, BUNDLED_SKILLS_DIR):
        for ext in (".md", ".txt", ""):
            candidate = skills_dir / f"{name}{ext}"
            if candidate.exists() and candidate.is_file():
                content = candidate.read_text(encoding="utf-8")
                return Skill(
                    name=name,
                    path=candidate,
                    description=_parse_description(content),
                    when_to_use=_parse_when_to_use(content),
                    prompt=content,
                )
    return None


def list_skills() -> list[Skill]:
    """列出所有可用 skill"""
    seen: set[str] = set()
    skills: list[Skill] = []
    for skills_dir in (USER_SKILLS_DIR, BUNDLED_SKILLS_DIR):
        if not skills_dir.exists():
            continue
        for path in sorted(skills_dir.glob("*.md")):
            name = path.stem
            if name not in seen:
                seen.add(name)
                content = path.read_text(encoding="utf-8")
                skills.append(Skill(
                    name=name,
                    path=path,
                    description=_parse_description(content),
                    when_to_use=_parse_when_to_use(content),
                    prompt=content,
                ))
    return skills


def save_skill(name: str, content: str, when_to_use: str | None = None) -> Path:
    """保存一个新 skill 到用户目录；当提供 when_to_use 时，会注入或合并到 YAML frontmatter。"""
    USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[^\w\-]', '_', name).strip('_')
    path = USER_SKILLS_DIR / f"{safe_name}.md"

    # 如果没有提供 when_to_use，直接写入
    if not when_to_use:
        path.write_text(content, encoding="utf-8")
        return path

    # 已有 frontmatter 且包含 when_to_use，则不修改 content
    fm = _parse_frontmatter(content)
    if fm.get("when_to_use"):
        path.write_text(content, encoding="utf-8")
        return path

    # 构造安全的 YAML 值（简单转义双引号）
    safe_val = when_to_use.replace('"', '\\"')

    if content.startswith("---"):
        # 有 frontmatter，但缺 when_to_use：在 frontmatter 起始后注入一行
        end = content.find("\n---", 3)
        if end != -1:
            fm_text = content[3:end].strip()
            body = content[end + 4:]
            new_fm = f"---\nwhen_to_use: \"{safe_val}\"\n"
            if fm_text:
                new_fm += fm_text.strip() + "\n"
            new_fm += "---\n"
            new_content = new_fm + body.lstrip("\n")
            path.write_text(new_content, encoding="utf-8")
            return path

    # 无 frontmatter：在顶部添加一个新的 frontmatter 块
    new_content = f"---\nwhen_to_use: \"{safe_val}\"\n---\n\n" + content.lstrip("\n")
    path.write_text(new_content, encoding="utf-8")
    return path


def init_bundled_skills() -> None:
    """创建内置 skill 目录和默认 skill 文件"""
    BUNDLED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    bundled = {
        "code-review": """# Code Review Expert
你是一位严格的代码审查专家。请对给定的代码进行全面审查，关注：
1. 逻辑错误和潜在 bug
2. 代码风格和可读性
3. 性能问题
4. 安全隐患
5. 测试覆盖情况
用中文给出具体、可操作的改进建议。
""",
        "debug": """# Debug Assistant
你是一位调试专家。当用户遇到错误时：
1. 先读取相关文件理解上下文
2. 分析错误信息的根本原因
3. 提出 2-3 种可能的修复方案
4. 选择最优方案并实施
5. 运行测试验证修复
不要猜测，先看代码再下结论。
""",
        "explain": """# Code Explainer
你是一位善于解释代码的老师。请用简洁的中文解释代码：
1. 整体功能是什么
2. 关键设计决策
3. 重要的数据流
4. 可能容易误解的地方
对初学者友好，用类比和例子辅助说明。
""",
        "write-tests": """# Test Writer
你是一位测试驱动开发专家。请为给定代码编写测试：
1. 先读取源码理解功能
2. 识别关键的测试场景（正常、边界、异常）
3. 使用 pytest 编写清晰的测试
4. 运行测试确认通过
测试名称要描述"做了什么、期望什么"。
""",
        "summarize": """# Summarizer
请对给定内容进行简洁总结：
- 核心要点（3-5条）
- 关键数据或结论
- 行动建议（如适用）
用中文，控制在200字以内。
""",
    }

    for name, content in bundled.items():
        path = BUNDLED_SKILLS_DIR / f"{name}.md"
        if not path.exists():
            path.write_text(content, encoding="utf-8")
