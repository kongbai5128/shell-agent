from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..query import QueryEngine
from ..sessions import list_sessions


@dataclass(frozen=True)
class CommandResult:
    output: str
    should_exit: bool = False
    clear_screen: bool = False


HELP_TEXT = """
可用斜杠命令：

  /help              显示此帮助
  /status            显示当前会话状态（model、token 用量、消息数）
  /cost              显示 token 用量和估算费用
  /model [名称]       查看或切换模型
  /mode [模式]        查看或切换权限模式（read-only / workspace-write / full-access）
  /compact           压缩历史对话，节省 token
  /clear             清空当前会话历史，开始新对话
  /sessions          列出已保存的会话
  /save              保存当前会话
  /exit              退出

  --- 记忆系统 ---
  /memory            列出所有记忆
  /memory search <词> 搜索相关记忆
  /memory add <内容>  手动添加一条记忆
  /memory dream      手动触发记忆整合（Dream）

  --- Skill 系统 ---
  /skills            列出所有可用 skill
  /skill <名称>       查看 skill 内容

  --- 多 Agent ---
  /agents            列出所有 Agent 及状态
  /coordinator       进入 Coordinator 模式（多 Agent 编排）

  --- 文件安全 ---
  /undo [路径]        撤销对指定文件的最近一次 AI 编辑（恢复到上一个备份版本）
""".strip()


def handle_command(cmd_line: str, engine: "QueryEngine") -> CommandResult | None:
    """
    处理斜杠命令，返回 CommandResult；
    若不是斜杠命令返回 None（交给 agent 处理）。
    对标 claw-code src/commands.py。
    """
    if not cmd_line.startswith("/"):
        return None

    parts = cmd_line.strip().split(maxsplit=2)
    cmd = parts[0].lower()
    arg1 = parts[1] if len(parts) > 1 else ""
    arg2 = parts[2] if len(parts) > 2 else ""

    # ── 基础命令 ───────────────────────────────────────────────

    if cmd == "/help":
        return CommandResult(HELP_TEXT)

    elif cmd == "/status":
        lines = [
            f"会话 ID : {engine.session_id}",
            f"模型    : {engine.model}",
            f"Provider: {engine.provider}",
            f"权限模式 : {engine.permission_ctx.mode.value}",
            f"消息数   : {len(engine.messages)}",
            f"输入 token : {engine.total_input_tokens:,}",
            f"输出 token : {engine.total_output_tokens:,}",
        ]
        return CommandResult("\n".join(lines))

    elif cmd == "/cost":
        total = engine.total_input_tokens + engine.total_output_tokens
        cost = (
            engine.total_input_tokens / 1_000_000 * 15
            + engine.total_output_tokens / 1_000_000 * 75
        )
        lines = [
            f"输入 token : {engine.total_input_tokens:,}",
            f"输出 token : {engine.total_output_tokens:,}",
            f"合计 token : {total:,}",
            f"估算费用   : ${cost:.4f} USD",
        ]
        return CommandResult("\n".join(lines))

    elif cmd == "/model":
        if not arg1:
            return CommandResult(f"当前模型: {engine.model}")
        engine.model = arg1
        return CommandResult(f"已切换模型: {arg1}")

    elif cmd == "/mode":
        from ..permissions import PermissionMode, ToolPermissionContext
        if not arg1:
            return CommandResult(f"当前权限模式: {engine.permission_ctx.mode.value}")
        try:
            mode = PermissionMode(arg1)
            engine.permission_ctx = ToolPermissionContext.from_mode(mode)
            return CommandResult(f"已切换权限模式: {mode.value}")
        except ValueError:
            return CommandResult(
                f"未知模式 '{arg1}'，可选: read-only / workspace-write / full-access"
            )

    elif cmd == "/compact":
        engine.compact()
        return CommandResult("已压缩历史对话，token 已节省。")

    elif cmd == "/clear":
        engine.messages.clear()
        engine.total_input_tokens = 0
        engine.total_output_tokens = 0
        from uuid import uuid4
        engine.session_id = uuid4().hex[:12]
        return CommandResult("已清空对话历史，开始新会话。", clear_screen=True)

    elif cmd == "/save":
        sid = engine.save()
        return CommandResult(f"会话已保存，ID: {sid}")

    elif cmd == "/sessions":
        sessions = list_sessions()
        if not sessions:
            return CommandResult("暂无已保存的会话")
        lines = ["已保存的会话:"]
        for s in sessions[:10]:
            lines.append(
                f"  {s.session_id}  |  {s.model}  |  "
                f"{s.input_tokens + s.output_tokens:,} tokens  |  {len(s.messages)} 条消息"
            )
        return CommandResult("\n".join(lines))

    elif cmd in ("/exit", "/quit", "/q"):
        return CommandResult("再见！", should_exit=True)

    # ── 记忆系统命令 ───────────────────────────────────────────

    elif cmd == "/memory":
        from ..memory import MemoryStore
        store = MemoryStore()

        if arg1 == "search" and arg2:
            memories = store.find_relevant(arg2, top_k=5)
            if not memories:
                return CommandResult(f"未找到与 '{arg2}' 相关的记忆")
            lines = [f"找到 {len(memories)} 条相关记忆："]
            for m in memories:
                lines.append(f"\n  [{m.id}] ⭐{m.importance} {', '.join(m.tags)}")
                lines.append(f"  {m.content[:150]}")
            return CommandResult("\n".join(lines))

        elif arg1 == "add":
            content = arg2.strip()
            if not content:
                return CommandResult("用法：/memory add <记忆内容>")
            import uuid
            from datetime import datetime, timezone
            from ..memory import Memory
            mem = Memory(
                id=f"mem-{uuid.uuid4().hex[:8]}",
                content=content,
                tags=[],
                created_at=datetime.now(timezone.utc).isoformat(),
                source_session=engine.session_id,
                importance=3,
            )
            store.save(mem)
            return CommandResult(f"记忆已保存：{mem.id}")

        elif arg1 == "dream":
            return CommandResult("正在启动 Dream 记忆整合，请稍候...")

        else:
            # 列出所有记忆
            memories = store.load_all()
            if not memories:
                return CommandResult("暂无记忆。使用 /memory add <内容> 添加。")
            idx = store.get_index()
            lines = [f"共 {len(memories)} 条记忆（上次整合: {idx.last_consolidated_at or '从未'}）："]
            for m in memories[:20]:
                lines.append(f"  [{m.id}] ⭐{m.importance} {m.content[:80]}")
            return CommandResult("\n".join(lines))

    # ── Skill 命令 ─────────────────────────────────────────────

    elif cmd == "/skills":
        from ..skills import list_skills
        skills = list_skills()
        if not skills:
            return CommandResult("暂无 skill。使用 skill_save 工具创建。")
        lines = ["可用 skill："]
        for s in skills:
            src = "内置" if "bundled" in str(s.path) else "用户"
            lines.append(f"  [{src}] {s.name}: {s.description}")
        return CommandResult("\n".join(lines))

    elif cmd == "/skill":
        from ..skills import load_skill
        if not arg1:
            return CommandResult("用法：/skill <名称>")
        skill = load_skill(arg1)
        if not skill:
            return CommandResult(f"未找到 skill '{arg1}'")
        return CommandResult(f"[{skill.name}]\n{skill.description}\n\n{skill.prompt[:500]}...")

    # ── 多 Agent 命令 ──────────────────────────────────────────

    elif cmd == "/agents":
        from ..coordinator import list_agents
        return CommandResult(list_agents())

    elif cmd == "/coordinator":
        return CommandResult(
            "Coordinator 模式：在对话中直接说'用多 agent 模式完成：<任务>'即可。\n"
            "或者发送消息时包含'并行'、'多agent'、'协调'等关键词触发。\n"
            "Coordinator 工具：spawn_agent / get_agent_result / shutdown_agent"
        )

    elif cmd == "/undo":
        if not arg1:
            return CommandResult("用法：/undo <文件路径>  — 撤销对指定文件的最近一次 AI 编辑")
        from ..tools.file_ops import undo_last_edit
        return CommandResult(undo_last_edit(arg1))

    else:
        return CommandResult(f"未知命令: {cmd}，输入 /help 查看可用命令")
