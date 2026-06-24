from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import ToolSpec, register

# todo 存储路径
TODO_FILE = Path(".shell-agent-todos.json")

TodoStatus = Literal["pending", "in_progress", "completed"]


@dataclass
class TodoItem:
    """
    对标 claw-code src/utils/todo/types.ts 的 TodoItemSchema。

    字段说明（来自 Claude Code prompt.ts）：
      content    — 祈使句形式，描述要做什么（例："Run tests"）
      status     — pending / in_progress / completed
      activeForm — 现在进行时形式，用于 spinner 显示（例："Running tests"）
                   帮助用户理解当前正在做什么

    注意：
      - id 由系统自动分配（LLM 无需管理），与 Claude Code v1 一致
      - 去掉了 priority 字段 —— Claude Code 不使用优先级，优先级排序
        会导致 LLM 在更新时频繁引入不必要的字段，增加冗余
    """
    id: str
    content: str
    status: TodoStatus
    active_form: str = ""      # 现在进行时，对标 Claude Code activeForm

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status,
            "activeForm": self.active_form,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TodoItem":
        return cls(
            id=str(d.get("id", "")),
            content=d.get("content", ""),
            status=d.get("status", "pending"),
            active_form=d.get("activeForm", ""),
        )


def _load_todos() -> list[TodoItem]:
    if not TODO_FILE.exists():
        return []
    try:
        data = json.loads(TODO_FILE.read_text(encoding="utf-8"))
        return [TodoItem.from_dict(d) for d in data]
    except Exception:
        return []


def _save_todos(todos: list[TodoItem]) -> None:
    TODO_FILE.write_text(
        json.dumps([t.to_dict() for t in todos], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _render_todos(todos: list[TodoItem]) -> str:
    """渲染任务列表为可读文本（对标 Claude Code UI 风格）"""
    if not todos:
        return "（暂无任务）"
    icons = {"pending": "⬜", "in_progress": "🔄", "completed": "✅"}
    lines = []
    for t in todos:
        icon = icons.get(t.status, "?")
        # in_progress 时显示 activeForm（如果有），帮助理解当前进度
        label = t.active_form if (t.status == "in_progress" and t.active_form) else t.content
        lines.append(f"  {icon} {label}")
    return "\n".join(lines)


def _validate_todos(todos: list[TodoItem]) -> str | None:
    """
    对标 claw-code 的 validate_todos 逻辑。
    返回 None 表示合法，返回字符串表示错误原因。

    规则（来自 Claude Code prompt.ts）：
      1. content 和 activeForm 不能为空
      2. 同一时刻最多一个 in_progress（"limit to ONE task at a time"）
    """
    if not todos:
        return "todos 列表不能为空"
    for t in todos:
        if not t.content.strip():
            return f"任务内容（content）不能为空（id={t.id}）"
        if not t.active_form.strip():
            return f"activeForm 不能为空，请提供现在进行时描述（id={t.id}，content={t.content!r}）"
    in_progress_count = sum(1 for t in todos if t.status == "in_progress")
    if in_progress_count > 1:
        return (
            f"同一时刻只能有 1 个 in_progress 任务，当前有 {in_progress_count} 个。"
            "请先完成当前任务再开始下一个。"
        )
    return None


def _todo_write(inp: dict) -> str:
    """
    对标 claw-code TodoWriteTool.call()。

    核心逻辑（来自 TodoWriteTool.ts）：
      - 接收完整 todos 列表（全量替换，Claude Code: "updated todo list"）
      - 全部完成时 newTodos = []（自动清空，对标 allDone ? [] : todos）
      - 完成 3+ 任务且无验证步骤时给出 verification nudge
      - 返回固定提示，不打印新旧对比（Claude Code: "Todos have been modified
        successfully. Ensure that you continue to use the todo list to track
        your progress."）
    """
    raw_todos = inp.get("todos", [])

    # 构建列表，id 由系统按顺序分配（LLM 无需管理 ID）
    new_todos: list[TodoItem] = []
    for i, item in enumerate(raw_todos):
        if isinstance(item, str):
            # 容错：允许直接传字符串
            new_todos.append(TodoItem(
                id=str(i + 1),
                content=item,
                status="pending",
                active_form=item,  # fallback：与 content 相同
            ))
        else:
            content = item.get("content", "").strip()
            active_form = item.get("activeForm", "").strip()
            # 容错：若 activeForm 未提供，用 content 作为回退
            new_todos.append(TodoItem(
                id=str(i + 1),
                content=content,
                status=item.get("status", "pending"),
                active_form=active_form or content,
            ))

    # 验证（对标 Claude Code validate_todos）
    err = _validate_todos(new_todos)
    if err:
        return f"错误：{err}"

    # 全部完成时自动清空（对标 Claude Code: allDone ? [] : todos）
    all_done = all(t.status == "completed" for t in new_todos)
    _save_todos([] if all_done else new_todos)

    # verification nudge（对标 Claude Code verificationNudgeNeeded 逻辑）
    # 当全部完成 && 任务数 >= 3 && 没有验证步骤时提醒
    nudge = ""
    if all_done and len(new_todos) >= 3:
        has_verify = any(
            "verif" in t.content.lower()
            or "test" in t.content.lower()
            or "测试" in t.content
            or "验证" in t.content
            for t in new_todos
        )
        if not has_verify:
            nudge = (
                "\n\n注意：你刚完成了 3+ 个任务，但没有验证步骤。"
                "在写最终总结前，建议运行测试或手动验证结果。"
            )

    # 固定返回（对标 Claude Code mapToolResultToToolResultBlockParam）
    base = "任务列表已更新。请继续使用 todo 工具跟踪进度，并推进当前任务。"
    # 显示当前列表，便于观察进度
    current = [] if all_done else new_todos
    if current:
        base += "\n\n" + _render_todos(current)
    else:
        base += "\n\n（所有任务已完成，列表已清空）"
    return base + nudge


def _todo_read(inp: dict) -> str:
    """
    读取当前任务列表。

    对标 Claude Code 在每轮对话开始时注入 todo 内容的做法
    （src/utils/messages.ts: "Here are the existing contents of your todo list"）。
    返回格式清晰，包含统计便于模型判断下一步。
    """
    todos = _load_todos()
    if not todos:
        return "当前没有待处理的任务。"

    pending = sum(1 for t in todos if t.status == "pending")
    in_progress = sum(1 for t in todos if t.status == "in_progress")
    completed = sum(1 for t in todos if t.status == "completed")

    lines = [
        f"当前任务列表（{pending} 待处理 / {in_progress} 进行中 / {completed} 已完成）：",
        _render_todos(todos),
    ]
    if in_progress == 0 and pending > 0:
        lines.append("\n提示：当前没有 in_progress 任务，请用 todo_write 将下一个任务标记为 in_progress。")
    return "\n".join(lines)


register(ToolSpec(
    name="todo_write",
    description=(
        "写入/更新当前会话的任务列表（全量替换）。"
        "复杂多步骤任务必须主动使用，保持进度可见。"
        "规则：(1) 同一时刻只能有一个 in_progress 任务；"
        "(2) 开始前标记 in_progress，完成后立即标记 completed；"
        "(3) 每项必须提供 content（祈使句：'运行测试'）"
        "和 activeForm（现在进行时：'正在运行测试'）。"
        "全部完成时列表自动清空。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": (
                    "完整的任务列表（全量替换当前列表）。"
                    "无需提供 id 字段，系统自动分配。"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": (
                                "祈使句形式，描述要做什么"
                                "（例：'运行测试'、'修复登录 bug'）。不能为空。"
                            ),
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": (
                                "任务状态。同一时刻只允许一个 in_progress。"
                            ),
                        },
                        "activeForm": {
                            "type": "string",
                            "description": (
                                "现在进行时形式，执行时显示在 spinner 中"
                                "（例：'正在运行测试'、'正在修复 bug'）。不能为空。"
                            ),
                        },
                    },
                    "required": ["content", "status", "activeForm"],
                },
            },
        },
        "required": ["todos"],
    },
    handler=_todo_write,
    dangerous=False,
))

register(ToolSpec(
    name="todo_read",
    description=(
        "读取当前任务列表，查看所有任务的状态与进度。"
        "开始新任务前或恢复会话时使用，确认当前应处理哪个任务。"
    ),
    input_schema={
        "type": "object",
        "properties": {},
    },
    handler=_todo_read,
    dangerous=False,
))
