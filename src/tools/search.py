from __future__ import annotations

import re
from pathlib import Path

from . import ToolSpec, register


def _glob_search(inp: dict) -> str:
    pattern = inp["pattern"]
    base = Path(inp.get("base_path", "."))
    try:
        matches = sorted(base.rglob(pattern))
        if not matches:
            return f"未找到匹配 '{pattern}' 的文件"
        lines = [f"找到 {len(matches)} 个文件:"]
        for m in matches[:50]:  # 最多显示 50 个
            lines.append(f"  {m}")
        if len(matches) > 50:
            lines.append(f"  ... 还有 {len(matches) - 50} 个")
        return "\n".join(lines)
    except Exception as e:
        return f"搜索失败: {e}"


def _grep_search(inp: dict) -> str:
    pattern = inp["pattern"]
    base = Path(inp.get("base_path", "."))
    file_pattern = inp.get("file_pattern", "*.py")
    case_sensitive = inp.get("case_sensitive", True)

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"无效的正则表达式: {e}"

    results = []
    for file in sorted(base.rglob(file_pattern)):
        if not file.is_file():
            continue
        try:
            lines = file.read_text(encoding="utf-8", errors="ignore").splitlines()
            for i, line in enumerate(lines, 1):
                if regex.search(line):
                    results.append(f"{file}:{i}: {line.rstrip()}")
        except Exception:
            continue

    if not results:
        return f"未在 '{file_pattern}' 文件中找到 '{pattern}'"

    output = [f"找到 {len(results)} 处匹配:"]
    output.extend(results[:100])
    if len(results) > 100:
        output.append(f"... 还有 {len(results) - 100} 处")
    return "\n".join(output)


register(ToolSpec(
    name="glob_search",
    description="按通配符模式递归搜索文件，例如 '**/*.py' 或 '*.md'。",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，如 '**/*.py'"},
            "base_path": {"type": "string", "description": "搜索起始目录，默认当前目录"},
        },
        "required": ["pattern"],
    },
    handler=_glob_search,
))

register(ToolSpec(
    name="grep_search",
    description="在文件内容中用正则表达式搜索文本，返回匹配的行。",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "base_path": {"type": "string", "description": "搜索起始目录，默认当前目录"},
            "file_pattern": {"type": "string", "description": "文件名模式，默认 '*.py'"},
            "case_sensitive": {"type": "boolean", "description": "是否区分大小写，默认 true"},
        },
        "required": ["pattern"],
    },
    handler=_grep_search,
))
