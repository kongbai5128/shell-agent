from __future__ import annotations

from . import ToolSpec, register
from ..skills import load_skill, list_skills, save_skill, init_bundled_skills

# 初始化内置 skill
init_bundled_skills()


def _skill_use(inp: dict) -> str:
    name = inp.get("skill", "").strip()
    args = inp.get("args", "")

    if not name:
        skills = list_skills()
        if not skills:
            return "暂无可用 skill。使用 skill_save 创建新 skill。"
        lines = ["可用 skill："]
        for s in skills:
            line = f"  - {s.name}: {s.description}"
            if s.when_to_use:
                line += f"\n    何时使用: {s.when_to_use}"
            lines.append(line)
        return "\n".join(lines)

    skill = load_skill(name)
    if not skill:
        return f"未找到 skill '{name}'。输入 skill_use（不带参数）查看可用列表。"

    result = f"[Skill: {skill.name}]\n{skill.description}\n\n{skill.prompt}"
    if args:
        result += f"\n\n[附加参数]\n{args}"
    return result


def _skill_save(inp: dict) -> str:
    """保存新 skill（when_to_use 可选，若提供会被注入或合并到文件 frontmatter）"""
    name = inp.get("name", "").strip()
    content = inp.get("content", "").strip()
    when_to_use = inp.get("when_to_use", "").strip() or None
    if not name:
        return "错误：必须提供 skill 名称"
    if not content:
        return "错误：必须提供 skill 内容"
    path = save_skill(name, content, when_to_use)
    if when_to_use:
        return f"Skill '{name}' 已保存到 {path}（已注入 when_to_use）"
    return f"Skill '{name}' 已保存到 {path}"


register(ToolSpec(
    name="skill_use",
    description=(
        "执行一个 skill（专家角色 prompt）。"
        "当用户请求与可用 skill 匹配时，这是强制要求：必须先调用此工具再进行任何其他响应。"
        "不带参数时列出所有可用 skill 及其用途说明。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "skill": {"type": "string", "description": "skill 名称，如 'code-review'、'debug'"},
            "args": {"type": "string", "description": "传给 skill 的附加参数（可选）"},
        },
    },
    handler=_skill_use,
    dangerous=False,
))

register(ToolSpec(
    name="skill_save",
    description="保存一个新的 skill 到用户目录，供后续复用。",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "skill 名称（英文，用连字符）"},
            "content": {"type": "string", "description": "skill 内容（Markdown 格式）"},
            "when_to_use": {"type": "string", "description": "何时使用该 skill 的说明"},
        },
        "required": ["name", "content","when_to_use"],
    },
    handler=_skill_save,
    dangerous=False,
))
