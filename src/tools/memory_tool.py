"""memory_tool — 让 LLM 在对话中主动保存记忆

对标 Claude Code：LLM 直接调用工具写入 MEMORY.md（或对应 JSON 文件），
不需要等到会话结束后的 Dream 批处理才生效。

何时使用 memory_save：
  - 用户纠正了你的做法，或确认某种做法有效 → type=feedback
  - 了解到用户身份/背景/偏好 → type=user
  - 了解到项目重要决策或背景 → type=project
  - 找到有用的外部资源（URL/路径）→ type=reference

何时不要保存：
  - 代码模式、架构、文件路径——可从代码读取
  - git 历史——git log 更权威
  - 临时状态、当前对话内容——仅当前会话有效
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import ToolSpec, register
from ..memory import Memory, MemoryStore, MEMORY_TYPES


def _memory_save(inp: dict) -> str:
    content = inp.get("content", "").strip()
    if not content:
        return "错误：content 不能为空"

    mem_type = inp.get("type", "project")
    if mem_type not in MEMORY_TYPES:
        return f"错误：type 必须是 {list(MEMORY_TYPES)} 之一，收到 '{mem_type}'"

    tags = inp.get("tags", [])
    if not isinstance(tags, list):
        tags = []

    importance = int(inp.get("importance", 3))
    importance = max(1, min(5, importance))

    store = MemoryStore()
    mem = Memory(
        id=f"mem-{uuid.uuid4().hex[:8]}",
        content=content,
        tags=tags,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_session="inline",
        importance=importance,
        type=mem_type,
    )
    store.save(mem)
    return f"已保存 [{mem_type}] 记忆（id={mem.id}，importance={importance}）"


register(ToolSpec(
    name="memory_save",
    description=(
        "将重要信息永久保存到记忆系统，以便在未来会话中调用。"
        "\n\n何时保存："
        "\n- 用户纠正了你的做法，或确认某种做法有效 → type=feedback"
        "\n- 了解到用户身份/背景/偏好 → type=user"
        "\n- 了解到项目重要决策或背景 → type=project"
        "\n- 找到有用的外部资源（URL/工具路径）→ type=reference"
        "\n\nfeedback 类型的 content 格式："
        "\n「规则：[规则本身]。\\n**Why:** [原因]。\\n**How to apply:** [应用场景]。」"
        "\n\n不要保存：代码模式/架构/文件路径（可从代码读取）、"
        "临时状态、当前对话内容。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "记忆内容。feedback 类型格式：'规则：[规则]。\\n**Why:** [原因]。\\n**How to apply:** [场景]。'"
                ),
            },
            "type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": "记忆类型：user/feedback/project/reference",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "标签列表，用于相关性搜索",
            },
            "importance": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "重要性 1-5（feedback 通常 4-5，user 通常 3-4）",
            },
        },
        "required": ["content", "type"],
    },
    handler=_memory_save,
    dangerous=False,
))
