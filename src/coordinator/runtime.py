"""
多 Agent 编排系统 — 对标 claw-code coordinator/coordinatorMode.ts

架构：
  Coordinator（指挥官）
    ├── 工具：spawn_agent / send_message / shutdown_agent / get_agent_result
    ├── 核心铁律：禁止甩锅式委派
    └── Worker 1, Worker 2, ... （真正并行——所有 Worker 同时跑在独立线程）

并行通知机制（对标 Claude Code）：
  - Worker 完成后自动把 <task-notification> XML 推入 _notification_queue
  - main.py 的 REPL loop 在每轮结束后消费队列，把通知喂给 Coordinator LLM
  - Coordinator 无需阻塞轮询——结果到了自然触发下一轮推理
  - get_agent_result 仍保留，用于 Coordinator 主动同步等待某个 Agent

对标逻辑：
  execute_agent()       → AgentWorker.run()
  spawn_agent_job()     → threading.Thread（立即启动，非阻塞）
  task-notification XML → _notification_queue + drain_notifications()
  build_agent_system_prompt() → WORKER_SYSTEM_PROMPT_TEMPLATE
  allowed_tools_for_subagent() → WORKER_TOOL_SETS
  agent_store_dir()     → AGENT_STORE_DIR
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from ..query import QueryEngine

# ── 存储路径，对标 claw-code agent_store_dir() ─────────────────
AGENT_STORE_DIR = Path(".shell-agents")

# ── 通知队列（对标 Claude Code task-notification 推送机制）──────
# Worker 完成后把 <task-notification> XML 推入此队列
# main.py REPL loop 调用 drain_notifications() 消费并喂给 Coordinator LLM
_notification_queue: queue.Queue[str] = queue.Queue()

# Worker 类型，对标 claw-code normalize_subagent_type()
WorkerType = Literal["general", "explore", "plan", "verification"]

# 每种 Worker 允许使用的工具，对标 claw-code allowed_tools_for_subagent()
WORKER_TOOL_SETS: dict[str, list[str]] = {
    "explore": ["read_file", "list_files", "glob_search", "grep_search", "web_fetch", "browser_navigate","browser_click",
    "browser_fill", "browser_get_content"],
    "plan": ["read_file", "list_files", "glob_search", "grep_search", "todo_write", "todo_read"],
    "verification": ["bash", "read_file", "glob_search", "grep_search", "todo_write"],
    "general": ["bash", "read_file", "write_file", "edit_file", "list_files", "browser_navigate","browser_click",
    "browser_fill", "browser_get_content", "glob_search", "grep_search", "web_fetch", "todo_write", "todo_read"],
}

# Coordinator 系统提示，对标 claw-code coordinatorMode.ts 的核心铁律
COORDINATOR_SYSTEM_PROMPT = """
你是一个多 Agent 编排的 Coordinator（指挥官）。

## 1. 你的职责

- 理解用户需求，将复杂任务分解为清晰的子任务
- 派发子任务给合适类型的 Worker Agent 执行
- 综合 Worker 的结果，向用户汇报
- 能直接回答的问题不必派发给 Worker

## 2. 你的工具

- **spawn_agent**：派发新任务给 Worker（立即在后台异步启动，不阻塞你）
- **send_message**：向已完成的 Worker 继续发送消息（复用其上下文）
- **get_agent_result**：主动等待某个 Worker 完成（一般不需要，结果会自动送达）
- **shutdown_agent**：终止一个 Worker

## 3. Worker 完成通知机制（重要！）

Worker 完成后会**自动推送通知**给你，格式如下：

```xml
<task-notification>
<task-id>{agent_id}</task-id>
<status>completed|failed</status>
<summary>Agent "xxx" completed / failed: error</summary>
<result>Worker 的最终输出</result>
</task-notification>
```

- 这些通知以"用户消息"形式送达，但它们是系统信号，不是真实用户输入
- 通过 `<task-notification>` 开头识别它们
- **不要** thank 或 acknowledge workers，直接向用户汇报新信息
- 所有 Worker 都完成后，综合结果向用户输出最终答案

## 4. 并行策略（并行是你的超能力）

spawn_agent 是**非阻塞的**——你一次可以 spawn 多个 Worker，它们真正并行运行：
- 只读任务（研究）：可自由并行
- 写文件任务（实现）：同一组文件同一时间只用一个 Worker
- 启动并行 Worker 后，简短告知用户启动了什么，然后等待通知自动到来

## 5. 核心铁律：禁止甩锅式委派

Worker 看不到你和用户的对话。每个 prompt 必须自包含。

**错误示例：**
- `"帮我处理一下这个文件"` ← 太模糊
- `"根据你的发现，实现修复"` ← 把理解工作推给 Worker

**正确示例：**
- `"读取 main.py 第 50-80 行，找出 parse_args 的参数列表，以 JSON 格式返回，不要修改文件"`
- `"修复 src/auth/validate.ts:42 的空指针。Session.expired 为 true 时 user 字段为 undefined，在访问 user.id 前加空值判断，返回 401。"`

## 6. continue vs spawn fresh

| 情况 | 操作 |
|------|------|
| 研究 Worker 已读取需要修改的文件 | **send_message** 继续，复用上下文 |
| 研究范围宽 但实现范围窄 | **spawn_agent** 新 Worker，避免噪音 |
| 修正失败/继续近期工作 | **send_message** 继续 |
| 验证刚实现的代码 | **spawn_agent** 新 Worker，保持独立视角 |

## 7. Worker 类型选择

- **explore**：只读任务（搜索、读取、网页抓取）
- **plan**：规划任务（分析需求、制定步骤）
- **verification**：验证任务（运行测试、检查结果）
- **general**：通用任务（需要写文件或执行命令）
"""

# Worker 系统提示，对标 claw-code build_agent_system_prompt()
WORKER_SYSTEM_PROMPT_TEMPLATE = """
你是一个后台 Worker Agent，类型为 `{worker_type}`。

你的任务由 Coordinator 分配，请：
1. 只完成分配的具体任务，不要扩展范围
2. 使用提供给你的工具完成任务
3. 不要向用户提问，遇到问题自行判断或说明无法完成
4. 最后输出简洁的结果摘要

允许使用的工具：{allowed_tools}

分配的任务：
{task_description}
"""


# ── Agent 状态 ─────────────────────────────────────────────────

AgentStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


@dataclass
class AgentManifest:
    """Agent 运行清单，对标 claw-code AgentOutput struct"""
    agent_id: str
    name: str
    description: str
    worker_type: str
    status: AgentStatus
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    output: str = ""
    error: str | None = None
    # 完整对话历史，send_message 续接时由 _run() 自动恢复
    messages: list = field(default_factory=list)
    model: str = ""
    provider: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentManifest":
        return cls(**d)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_manifest_path(agent_id: str) -> Path:
    return AGENT_STORE_DIR / f"{agent_id}.json"


def _write_manifest(manifest: AgentManifest) -> None:
    AGENT_STORE_DIR.mkdir(parents=True, exist_ok=True)
    _agent_manifest_path(manifest.agent_id).write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_manifest(agent_id: str) -> AgentManifest | None:
    path = _agent_manifest_path(agent_id)
    if not path.exists():
        return None
    try:
        return AgentManifest.from_dict(json.loads(path.read_text()))
    except Exception:
        return None


# ── Worker Agent ───────────────────────────────────────────────

class AgentWorker:
    """
    在独立线程中运行的 Worker Agent。
    对标 claw-code spawn_agent_job() + run_agent_job()。
    """

    def __init__(
        self,
        agent_id: str,
        task: str,
        worker_type: str,
        engine: "QueryEngine",
    ):
        self.agent_id = agent_id
        self.task = task
        self.worker_type = worker_type
        self.engine = engine
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """在后台线程启动 Worker，对标 claw-code spawn_agent_job()"""
        self._thread = threading.Thread(
            target=self._run,
            name=f"worker-{self.agent_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        """Worker 主循环，对标 claw-code run_agent_job()"""
        manifest = _read_manifest(self.agent_id)
        if not manifest:
            return

        manifest.status = "running"
        manifest.started_at = _now_iso()
        _write_manifest(manifest)

        try:
            from .. import tools as tools_module
            from ..permissions import PermissionMode, ToolPermissionContext
            from ..query import QueryEngine, TurnResult

            # 只允许该 Worker 类型的工具
            allowed = set(WORKER_TOOL_SETS.get(self.worker_type, WORKER_TOOL_SETS["general"]))

            # 构建 Worker 专用的权限上下文
            all_tool_names = {t.name for t in tools_module.all_tools()}
            denied = all_tool_names - allowed
            perm = ToolPermissionContext(
                mode=PermissionMode.FULL_ACCESS,
                deny_names=frozenset(denied),
            )

            # 构建 Worker 系统提示
            system_prompt = WORKER_SYSTEM_PROMPT_TEMPLATE.format(
                worker_type=self.worker_type,
                allowed_tools=", ".join(sorted(allowed)),
                task_description=self.task,
            )

            # 创建独立的 QueryEngine 实例（Worker 有自己的对话历史）
            worker_engine = QueryEngine(
                model=self.engine.model,
                provider=self.engine.provider,
                permission_ctx=perm,
                system_prompt=system_prompt,
            )

            # send_message 续接场景：manifest.messages 已有历史，直接恢复
            if manifest.messages:
                worker_engine.messages = list(manifest.messages)

            # 执行任务
            result: TurnResult = worker_engine.run_turn(self.task)
            output = result.output

            manifest.status = "completed"
            manifest.completed_at = _now_iso()
            manifest.output = output
            manifest.messages = worker_engine.messages  # 持久化完整历史供下次续接
            _write_manifest(manifest)

            # 对标 Claude Code：Worker 完成后自动推送 task-notification
            # Coordinator REPL loop 消费此队列，无需主动轮询
            _notification_queue.put(_build_task_notification(
                agent_id=self.agent_id,
                name=manifest.name,
                status="completed",
                summary=f'Agent "{manifest.name}" completed',
                result=output,
            ))

        except Exception as e:
            manifest.status = "failed"
            manifest.completed_at = _now_iso()
            manifest.error = str(e)
            _write_manifest(manifest)

            _notification_queue.put(_build_task_notification(
                agent_id=self.agent_id,
                name=manifest.name,
                status="failed",
                summary=f'Agent "{manifest.name}" failed: {e}',
            ))

    def wait(self, timeout: float = 120.0) -> AgentManifest | None:
        """等待 Worker 完成，对标 claw-code 的轮询机制"""
        if self._thread:
            self._thread.join(timeout=timeout)
        return _read_manifest(self.agent_id)


# ── Coordinator 工具 ───────────────────────────────────────────
# 这些工具注册后供 Coordinator 模式下的 Claude 调用

_active_workers: dict[str, AgentWorker] = {}


def _build_task_notification(
    agent_id: str,
    name: str,
    status: str,
    summary: str,
    result: str = "",
) -> str:
    """
    构建 <task-notification> XML，对标 Claude Code coordinator 协议。
    Worker 完成后推入 _notification_queue；REPL loop 消费并注入给 Coordinator LLM。
    """
    result_block = f"\n<result>{result}</result>" if result else ""
    return (
        f"<task-notification>\n"
        f"<task-id>{agent_id}</task-id>\n"
        f"<status>{status}</status>\n"
        f"<summary>{summary}</summary>"
        f"{result_block}\n"
        f"</task-notification>"
    )


def drain_notifications() -> list[str]:
    """
    消费所有待处理的 task-notification（非阻塞）。
    对标 Claude Code 中 worker 结果以 user-role message 推送给 Coordinator 的机制。
    由 main.py REPL loop 在每轮 LLM 回复后调用。
    """
    notifications = []
    while True:
        try:
            notifications.append(_notification_queue.get_nowait())
        except queue.Empty:
            break
    return notifications


def spawn_agent(inp: dict, engine: "QueryEngine") -> str:
    """
    派发任务给 Worker Agent。
    对标 claw-code execute_agent()。
    """
    description = inp.get("description", "").strip()
    task = inp.get("task", "").strip()
    worker_type = inp.get("worker_type", "general")

    if not description:
        return "错误：必须提供 description（任务简介）"
    if not task:
        return "错误：必须提供 task（具体任务内容）"

    # 铁律检查：任务描述不能过于模糊
    if len(task) < 20:
        return f"错误：任务描述太简短（{len(task)} 字符），必须提供具体、清晰的任务内容，禁止甩锅式委派"

    if worker_type not in WORKER_TOOL_SETS:
        worker_type = "general"

    agent_id = f"agent-{uuid.uuid4().hex[:8]}"
    name = description[:32].replace(" ", "-")

    manifest = AgentManifest(
        agent_id=agent_id,
        name=name,
        description=description,
        worker_type=worker_type,
        status="pending",
        created_at=_now_iso(),
        model=engine.model,
        provider=engine.provider,
    )
    _write_manifest(manifest)

    worker = AgentWorker(
        agent_id=agent_id,
        task=task,
        worker_type=worker_type,
        engine=engine,
    )
    _active_workers[agent_id] = worker
    worker.start()

    return json.dumps({
        "agent_id": agent_id,
        "name": name,
        "worker_type": worker_type,
        "status": "running",
        "message": f"Worker {agent_id} 已在后台启动，完成后结果将通过 <task-notification> 自动送达",
    }, ensure_ascii=False, indent=2)


def send_message(inp: dict, engine: "QueryEngine") -> str:
    """
    向已有 Worker 续接消息，复用 manifest 中持久化的完整对话历史。
    与 spawn_agent 一样是异步/非阻塞的：续接 Worker 在后台线程运行，
    结果通过 _notification_queue 自动推送，对标 Claude Code resumeAgentBackground()。
    """
    agent_id = inp.get("agent_id", "").strip()
    message = inp.get("message", "").strip()

    if not agent_id:
        return "错误：必须提供 agent_id"
    if not message:
        return "错误：必须提供 message"

    manifest = _read_manifest(agent_id)
    if not manifest:
        return f"错误：未找到 Agent {agent_id}"

    if manifest.status == "running":
        return f"错误：Worker {agent_id} 仍在运行中，请等待完成通知后再调用 send_message"

    if manifest.status not in ("completed", "failed"):
        return f"错误：Agent {agent_id} 状态为 {manifest.status}，无法续接"

    # 重置状态；manifest.messages 保留不动，_run() 会从中恢复历史
    manifest.status = "pending"
    manifest.started_at = None
    _write_manifest(manifest)

    resume_worker = AgentWorker(
        agent_id=agent_id,
        task=message,
        worker_type=manifest.worker_type,
        engine=engine,
    )
    _active_workers[agent_id] = resume_worker
    resume_worker.start()  # 非阻塞，结果通过 _notification_queue 自动推送

    return json.dumps({
        "agent_id": agent_id,
        "status": "running",
        "message": f"续接消息已发送，Worker {agent_id} 已在后台重新启动，结果将通过 <task-notification> 自动送达",
    }, ensure_ascii=False, indent=2)


def get_agent_result(inp: dict) -> str:
    """
    获取 Worker 的执行结果，支持等待。
    对标 claw-code 的 Agent 状态轮询。
    """
    agent_id = inp.get("agent_id", "").strip()
    wait_seconds = inp.get("wait_seconds", 10)

    if not agent_id:
        return "错误：必须提供 agent_id"

    worker = _active_workers.get(agent_id)
    if worker:
        manifest = worker.wait(timeout=float(wait_seconds))
    else:
        # 从文件读取
        manifest = _read_manifest(agent_id)

    if not manifest:
        return f"错误：未找到 Agent {agent_id}"

    result = {
        "agent_id": manifest.agent_id,
        "name": manifest.name,
        "status": manifest.status,
        "worker_type": manifest.worker_type,
    }
    if manifest.status == "completed":
        result["output"] = manifest.output
    elif manifest.status == "failed":
        result["error"] = manifest.error or "未知错误"
    elif manifest.status == "running":
        result["message"] = "当前Worker 仍在运行，若存在其他子agent，可先查询其他子agent或稍后再查询"

    return json.dumps(result, ensure_ascii=False, indent=2)


def shutdown_agent(inp: dict) -> str:
    """终止一个 Worker Agent"""
    agent_id = inp.get("agent_id", "").strip()
    if not agent_id:
        return "错误：必须提供 agent_id"

    manifest = _read_manifest(agent_id)
    if not manifest:
        return f"错误：未找到 Agent {agent_id}"

    manifest.status = "cancelled"
    manifest.completed_at = _now_iso()
    _write_manifest(manifest)

    if agent_id in _active_workers:
        del _active_workers[agent_id]

    return f"Agent {agent_id} 已终止"


def list_agents() -> str:
    """列出所有 Agent 及其状态"""
    if not AGENT_STORE_DIR.exists():
        return "暂无 Agent 记录"
    manifests = []
    for p in sorted(AGENT_STORE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            m = AgentManifest.from_dict(json.loads(p.read_text()))
            manifests.append(m)
        except Exception:
            continue

    if not manifests:
        return "暂无 Agent 记录"

    icons = {"pending": "⏳", "running": "🔄", "completed": "✅", "failed": "❌", "cancelled": "🚫"}
    lines = [f"共 {len(manifests)} 个 Agent："]
    for m in manifests[:20]:
        icon = icons.get(m.status, "?")
        lines.append(f"  {icon} [{m.agent_id}] {m.name} ({m.worker_type})")
    return "\n".join(lines)


# ── Coordinator 模式入口 ────────────────────────────────────────

def build_coordinator_engine(base_engine: "QueryEngine") -> "QueryEngine":
    """
    构建 Coordinator 专用的 QueryEngine：
    - 替换 system prompt 为 Coordinator 模式
    - 只注册 Coordinator 的四个工具（spawn/send_message/get_result/shutdown）
    对标 claw-code coordinatorMode.ts 的 feature flag 机制。
    """
    from ..query import QueryEngine
    from ..permissions import PermissionMode, ToolPermissionContext
    from .. import tools as tools_module

    # Coordinator 只允许使用编排工具，屏蔽所有普通工具
    all_tools = {t.name for t in tools_module.all_tools()}
    coordinator_tools = {"spawn_agent", "send_message", "get_agent_result", "shutdown_agent"}
    denied = all_tools - coordinator_tools

    perm = ToolPermissionContext(
        mode=PermissionMode.FULL_ACCESS,
        deny_names=frozenset(denied),
    )

    coord_engine = QueryEngine(
        model=base_engine.model,
        provider=base_engine.provider,
        permission_ctx=perm,
        system_prompt=COORDINATOR_SYSTEM_PROMPT,
    )
    return coord_engine


def register_coordinator_tools(engine: "QueryEngine") -> None:
    """
    将 Coordinator 工具注册到全局工具注册表。
    需要在启动 Coordinator 模式前调用。
    """
    from .. import tools as tools_module

    tools_module.register(tools_module.ToolSpec(
        name="spawn_agent",
        description=(
            "在独立线程中启动 Worker Agent 并行执行子任务（非阻塞）。"
            "【何时使用】用户明确要求用 agent/多代理，或任务可拆解为多个互不依赖的子任务时必须使用。"
            "【铁律】task 必须自包含、清晰完整（至少20字），禁止甩锅式委派。"
            "【结果获取】Worker 完成后结果以 <task-notification> XML 自动送达，无需调用 get_agent_result 轮询。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "任务简介（用于标识）"},
                "task": {"type": "string", "description": "具体任务内容（必须清晰完整，至少20字）"},
                "worker_type": {
                    "type": "string",
                    "enum": ["general", "explore", "plan", "verification"],
                    "description": "Worker 类型",
                    "default": "general",
                },
            },
            "required": ["description", "task"],
        },
        handler=lambda inp: spawn_agent(inp, engine),
        dangerous=False,
    ))

    tools_module.register(tools_module.ToolSpec(
        name="get_agent_result",
        description="等待并获取 Worker Agent 的执行结果。",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID"},
                "wait_seconds": {"type": "integer", "description": "最长等待秒数，默认 10", "default": 10},
            },
            "required": ["agent_id"],
        },
        handler=get_agent_result,
        dangerous=False,
    ))

    tools_module.register(tools_module.ToolSpec(
        name="shutdown_agent",
        description="终止一个正在运行的 Worker Agent。",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "要终止的 Agent ID"},
            },
            "required": ["agent_id"],
        },
        handler=shutdown_agent,
        dangerous=False,
    ))
    
    tools_module.register(tools_module.ToolSpec(
        name="send_message",
        description=(
            "向已完成的 Worker Agent 追加消息，复用其已有上下文继续工作。"
            "适合：研究完成后让同一 Worker 执行实现、纠正 Worker 的错误。"
            "对标 claw-code SendMessageTool。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Worker 的 agent_id"},
                "message": {"type": "string", "description": "追加的消息（综合后的实现规格）"},
            },
            "required": ["agent_id", "message"],
        },
        handler=lambda inp: send_message(inp, engine),
        dangerous=False,
    ))

    tools_module.register(tools_module.ToolSpec(
        name="list_agents",
        description="列出所有 Worker Agent 及其当前状态（pending/running/completed/failed/cancelled）。用于查看当前有哪些 Agent 在运行或已完成。",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=lambda inp: list_agents(),
        dangerous=False,
    ))
