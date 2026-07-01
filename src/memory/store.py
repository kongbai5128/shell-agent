"""
记忆系统 — 对标 claw-code memdir/ 子系统

三个功能：
1. MemoryStore     — 读写结构化记忆文件（using/memory/）
2. auto_consolidate — 自动整合历史记录（距上次整合超24h且有1+新会话）
3. Dream           — 四阶段记忆整合子 agent（Orient→Gather→Consolidate→Prune）

记忆类型（对标 claw-code memdir/memoryTypes.ts）：
  user      — 用户背景、角色、偏好、专业知识
  feedback  — 用户对 agent 行为的纠正或确认（最重要！）
  project   — 项目背景、决策、进展（默认类型）
  reference — 外部资源指针（文档链接、工具位置等）

对标文件：
  memdir/memdir.ts          → MemoryStore
  memdir/findRelevantMemories.ts → find_relevant_memories()
  memdir/memoryAge.ts       → memory_age_hours()
  memdir/memoryScan.ts      → scan_new_sessions()
  memdir/teamMemPrompts.ts  → DREAM_PROMPTS
  memdir/memoryTypes.ts     → MEMORY_TYPES, MemoryType
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from ..paths import MEMORY_DIR, MEMORY_INDEX_FILE, SESSIONS_DIR

if TYPE_CHECKING:
    from ..query import QueryEngine

# ── 记忆类型（对标 claw-code memdir/memoryTypes.ts）─────────────
MEMORY_TYPES = ("user", "feedback", "project", "reference")
MemoryType = Literal["user", "feedback", "project", "reference"]

# Dream 触发条件（对标 claw-code 的触发逻辑）
DREAM_MIN_NEW_SESSIONS = 1       # 至少 1 个新会话
DREAM_MIN_HOURS_SINCE_LAST = 24  # 距上次整合至少 24 小时

# 对标 claw-code findRelevantMemories.ts → SELECT_MEMORIES_SYSTEM_PROMPT
_SELECT_MEMORIES_PROMPT = """\
你是一个记忆检索助手。根据用户的查询，从可用记忆列表中选出"明确有帮助"的记忆 ID（最多 {top_k} 条）。

记忆类型说明：
- [feedback] 用户对 agent 行为的纠正/确认 — 若查询场景可能触发相关行为，优先选入
- [user]     用户背景/偏好 — 若查询需要了解用户身份才能更好回答，选入
- [project]  项目背景/决策 — 若查询涉及项目状态或决策，选入
- [reference] 外部资源指针 — 若查询需要访问外部系统，选入

规则：
- 只选择你确定对处理当前查询有帮助的记忆，要有较强的选择性
- 如果不确定某条记忆是否有用，不要选它
- 如果没有任何记忆明确相关，返回空列表
- 若用户正在使用某工具，不选该工具的参考文档类记忆（已有上下文）；但若含警告/已知坑点，仍应选入

以 JSON 格式输出：{"selected_ids": ["id1", "id2"]}
""".strip()


# ── 数据结构 ───────────────────────────────────────────────────

def memory_age_text(created_at: str) -> str:
    """对标 claw-code memoryAge.ts — 返回人类可读的记忆新鲜度"""
    try:
        created = datetime.fromisoformat(created_at)
        days = (datetime.now(timezone.utc) - created).days
        if days == 0:
            return ""
        if days == 1:
            return "（昨天）"
        return f"（{days} 天前）"
    except Exception:
        return ""

@dataclass
class Memory:
    """单条记忆，对标 claw-code memdir/memoryTypes.ts 的 Memory 类型"""
    id: str
    content: str              # 记忆内容（自然语言）
    tags: list[str]           # 标签，用于相关性搜索
    created_at: str           # ISO 时间戳
    source_session: str       # 来源会话 ID
    importance: int = 3       # 1-5，5 最重要
    type: str = "project"     # user / feedback / project / reference

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Memory":
        # 兼容旧格式（无 type 字段）
        data = {**d}
        data.setdefault("type", "project")
        return cls(**data)


@dataclass
class MemoryIndex:
    """记忆索引，记录整合元数据，对标 claw-code memdir/paths.ts"""
    last_consolidated_at: str = ""    # 上次 Dream 时间
    total_memories: int = 0
    session_count_at_last_consolidation: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryIndex":
        return cls(**d)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _tokenize_text(text: str) -> set[str]:
    """轻量分词：英文按单词，中文按词串+双字片段。"""
    normalized = _normalize_text(text)
    tokens = set(re.findall(r"[a-z0-9_+\-]{2,}", normalized))

    for chunk in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(chunk) <= 4:
            tokens.add(chunk)
        else:
            tokens.add(chunk)
            tokens.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return tokens


def _freshness_factor(created_at: str) -> float:
    '''
    ≤ 7 天 → 1.0（满分）
    > 7 天 → max(0.6, 1.0 - days × 0.01)  线性衰减，最低保底 0.6
    '''
    try:
        created = datetime.fromisoformat(created_at)
        days_old = (datetime.now(timezone.utc) - created).days
        if days_old <= 7:
            return 1.0
        return max(0.6, 1.0 - days_old * 0.01)
    except Exception:
        return 1.0


def _score_memory_relevance(memory: "Memory", query: str) -> float:
    """更接近 Claude Code 的“筛选”思路：宁缺毋滥。"""
    query_text = _normalize_text(query)
    if not query_text:
        return 0.0

    query_tokens = _tokenize_text(query_text)
    if not query_tokens:
        return 0.0

    content_text = _normalize_text(memory.content)
    tags_text = _normalize_text(" ".join(memory.tags))
    combined_text = f"{content_text} {tags_text}".strip()
    combined_tokens = _tokenize_text(combined_text)
    token_overlap = query_tokens & combined_tokens
    exact_phrase_hit = len(query_text) >= 4 and query_text in combined_text
    tag_overlap = query_tokens & _tokenize_text(tags_text)

    # 没有任何命中时直接淘汰，不再仅凭 importance 入选。
    if not token_overlap and not exact_phrase_hit:
        return 0.0

    coverage = len(token_overlap) / max(len(query_tokens), 1)
    if not exact_phrase_hit and coverage < 0.2:
        return 0.0

    score = 0.0
    if exact_phrase_hit:
        score += 6.0
    score += len(token_overlap) * 2.0
    score += coverage * 4.0
    score += len(tag_overlap) * 1.5
    score += min(memory.importance, 5) * 0.35
    score *= _freshness_factor(memory.created_at)
    return score


# ── MemoryStore ────────────────────────────────────────────────

class MemoryStore:
    """
    记忆读写，对标 claw-code memdir/memdir.ts。
    每条记忆存为独立的 JSON 文件：using/memory/<id>.json
    """

    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, memory: Memory) -> Path:
        path = MEMORY_DIR / f"{memory.id}.json"
        path.write_text(
            json.dumps(memory.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._update_index(delta=1)
        return path

    def load_all(self) -> list[Memory]:
        memories = []
        for p in sorted(MEMORY_DIR.glob("*.json")):
            if p.name.startswith("_"):
                continue
            try:
                data = json.loads(p.read_text())
                memories.append(Memory.from_dict(data))
            except Exception:
                continue
        return memories

    def find_relevant(self, query: str, top_k: int = 5) -> list[Memory]:
        scored: list[tuple[float, Memory]] = []
        for m in self.load_all():
            score = _score_memory_relevance(m, query)
            if score > 0:
                scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:top_k]]

    def find_relevant_with_llm(
        self,
        query: str,
        engine: "QueryEngine",
        top_k: int = 5,
    ) -> list[Memory]:
        """
        对标 claw-code findRelevantMemories.ts：
        1. 扫描所有记忆，构建摘要清单（ID + 内容片段 + tags）
        2. 把 query + 清单发给 LLM，让它选择明确相关的 ID
        3. 允许返回空列表（无相关记忆时不强塞内容）
        4. LLM 调用失败时自动降级到本地词法检索
        """
        all_memories = self.load_all()
        if not all_memories:
            return []

        mem_by_id: dict[str, Memory] = {m.id: m for m in all_memories}
        manifest_lines: list[str] = []
        for m in all_memories:
            # 对标 claw-code memoryScan.ts formatMemoryManifest：[type] id: snippet [tags]
            snippet = m.content[:120].replace("\n", " ")
            tags_str = f"  [tags: {', '.join(m.tags)}]" if m.tags else ""
            manifest_lines.append(f"- [{m.type}] {m.id}: {snippet}{tags_str}")
        manifest = "\n".join(manifest_lines)

        try:
            resp = engine._client.chat.completions.create(
                model=engine.model,
                max_tokens=256,
                messages=[
                    {
                        "role": "system",
                        "content": _SELECT_MEMORIES_PROMPT.format(top_k=top_k),
                    },
                    {
                        "role": "user",
                        "content": f"查询：{query}\n\n可用记忆：\n{manifest}",
                    },
                ],
            )
            raw = resp.choices[0].message.content or ""
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                selected_ids: list[str] = data.get("selected_ids", [])
                return [mem_by_id[mid] for mid in selected_ids if mid in mem_by_id]
            return []  # LLM 返回空或无法解析 → 不注入
        except Exception:
            return self.find_relevant(query, top_k=top_k)  # 降级到词法检索

    def delete(self, memory_id: str) -> bool:
        path = MEMORY_DIR / f"{memory_id}.json"
        if path.exists():
            path.unlink()
            self._update_index(delta=-1)
            return True
        return False

    def prune_low_importance(self, threshold: int = 1) -> int:
        """删除重要度低于阈值的记忆，对标 claw-code Prune 阶段"""
        count = 0
        for m in self.load_all():
            if m.importance <= threshold:
                self.delete(m.id)
                count += 1
        return count

    def _load_index(self) -> MemoryIndex:
        if MEMORY_INDEX_FILE.exists():
            try:
                return MemoryIndex.from_dict(json.loads(MEMORY_INDEX_FILE.read_text()))
            except Exception:
                pass
        return MemoryIndex()

    def _update_index(self, delta: int = 0) -> None:
        idx = self._load_index()
        idx.total_memories = max(0, idx.total_memories + delta)
        MEMORY_INDEX_FILE.write_text(
            json.dumps(idx.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get_index(self) -> MemoryIndex:
        return self._load_index()

    def update_consolidation_timestamp(self, session_count: int) -> None:
        idx = self._load_index()
        idx.last_consolidated_at = datetime.now(timezone.utc).isoformat()
        idx.session_count_at_last_consolidation = session_count
        MEMORY_INDEX_FILE.write_text(
            json.dumps(idx.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# ── Dream 四阶段记忆整合 ───────────────────────────────────────

# 四阶段 prompt，对标 claw-code memdir/teamMemPrompts.ts
DREAM_PROMPTS = {
    "orient": """
你正在执行记忆整合任务的第一阶段：Orient（定向）。

请分析以下近期会话摘要，识别：
1. 用户最关注的主题领域
2. 反复出现的问题和模式
3. 已解决的重要问题
4. 仍未解决的问题

会话数据：
{session_summaries}

以 JSON 格式输出：
{{
  "themes": ["主题1", "主题2"],
  "recurring_problems": ["问题1"],
  "solved": ["已解决1"],
  "pending": ["未解决1"]
}}
""",

    "gather": """
你正在执行记忆整合任务的第二阶段：Gather（收集）。

根据 Orient 阶段的分析结果，从以下会话内容中提取值得长期记忆的信息。

Orient 分析结果：
{orient_result}

会话内容：
{session_content}

## 记忆类型（必须为每条记忆指定 type）

- **feedback**（最重要）：用户纠正过 agent 的做法，或明确确认某种做法有效。
  内容格式必须为：
  "规则：[规则本身]。\\n**Why:** [原因/背景]。\\n**How to apply:** [应用场景]。"
  示例："规则：不要在回复末尾总结刚做了什么。\\n**Why:** 用户说'我可以看 diff，不需要你重复'。\\n**How to apply:** 所有代码修改回复都不加尾部总结段落。"

- **user**：用户的身份、专业背景、技能水平、长期偏好。
  示例："用户是 Python 高级工程师，正在开发 shell-agent 项目（对标 Claude Code），偏好简洁回复。"

- **project**：项目背景、重要决策、当前状态（不可从代码读取的信息）。
  内容格式（重要时）："[事实]。\\n**Why:** [动机]。\\n**How to apply:** [影响决策的方式]。"

- **reference**：外部资源的位置和用途（文档 URL、数据库名、工具路径等）。
  示例："豆瓣 Top250 需用 browser_navigate，web_fetch 会被反爬拦截。"

## 不要保存的内容
- 代码模式、架构、文件路径——可从代码读取
- git 历史、谁修改了什么——git log 更权威
- 临时任务状态、当前对话内容
- 已在 AGENT.md 中记录的内容

以 JSON 格式输出记忆列表（每条必须有 type 字段）：
{{
  "memories": [
    {{
      "type": "feedback",
      "content": "规则：[规则]。\\n**Why:** [原因]。\\n**How to apply:** [应用场景]。",
      "tags": ["标签1", "标签2"],
      "importance": 5
    }},
    {{
      "type": "user",
      "content": "用户背景描述",
      "tags": ["用户背景"],
      "importance": 4
    }}
  ]
}}
""",

    "consolidate": """
你正在执行记忆整合任务的第三阶段：Consolidate（整合）。

请合并和去重以下记忆，消除重复，保留最有价值的版本：

现有记忆：
{existing_memories}

新提取的记忆：
{new_memories}

以 JSON 格式输出整合后的记忆列表（格式同 Gather 阶段）。
""",

    "prune": """
你正在执行记忆整合任务的第四阶段：Prune（修剪）。

请评估以下记忆的重要性，给每条打分（1-5），并标记应该删除的记忆：
- 5：非常重要，长期有价值
- 3：一般重要
- 1：过时或价值低，可以删除

记忆列表：
{memories}

以 JSON 格式输出：
{{
  "keep": [{{"id": "id", "importance": 4}}, ...],
  "delete": ["id1", "id2"]
}}
""",
}


def _count_sessions() -> int:
    if not SESSIONS_DIR.exists():
        return 0
    return len(list(SESSIONS_DIR.glob("*.json")))


def _format_session_digest(msgs: list[dict]) -> str:
    """
    把一个会话的 messages 列表转成可读的对话摘要，供 Dream 使用。

    对标 claw-code autoDream/consolidationPrompt.ts 的设计思路：
    Dream 需要看到完整的对话内容才能提取有意义的记忆，而不只是用户 query。
    包含：
      - user 消息全文（截断到 800 字符）
      - assistant 文字回复（截断到 400 字符，排除纯工具调用轮次）
      - tool_calls 工具名列表（知道做了什么，不需要看参数）
      - tool result 中的错误信息（值得记住的坑）
    """
    lines: list[str] = []
    for msg in msgs:
        role = msg.get("role", "")

        if role == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                text = content.strip()[:800]
                if len(content.strip()) > 800:
                    text += "…"
                lines.append(f"[用户] {text}")

        elif role == "assistant":
            # 文字回复
            text = (msg.get("content") or "").strip()
            if text:
                snippet = text[:400]
                if len(text) > 400:
                    snippet += "…"
                lines.append(f"[助手] {snippet}")
            # 工具调用名（知道做了什么操作）
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                lines.append(f"[工具调用] {', '.join(names)}")

        elif role == "tool":
            # 只记录看起来像错误的 tool result（含"错误"/"error"/"失败"/"failed"）
            content = msg.get("content", "")
            if isinstance(content, str):
                cl = content.lower()
                if any(kw in cl for kw in ("错误", "error", "失败", "failed", "exception", "traceback")):
                    snippet = content.strip()[:300]
                    if len(content.strip()) > 300:
                        snippet += "…"
                    lines.append(f"[工具错误] {snippet}")

    return "\n".join(lines)


def _summarize_sessions(limit: int = 10) -> str:
    """
    提取最近会话的完整对话摘要，用于 Dream。

    改进说明（对标 claw-code autoDream）：
    旧版只取 user 消息前 100 字，Dream LLM 看不到 assistant 的决策和工具行为，
    无法提取有意义的 feedback/project 类型记忆。
    新版提取完整对话摘要：user 全文 + assistant 回复 + 工具调用名 + 工具错误。
    """
    if not SESSIONS_DIR.exists():
        return "(无会话记录)"
    sessions = sorted(
        SESSIONS_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    summaries = []
    for p in sessions:
        try:
            data = json.loads(p.read_text())
            msgs = data.get("messages", [])
            if not msgs:
                continue
            digest = _format_session_digest(msgs)
            if digest.strip():
                summaries.append(f"=== 会话 {p.stem} ===\n{digest}")
        except Exception:
            continue
    return "\n\n".join(summaries) if summaries else "(无有效会话)"


def should_dream() -> bool:
    """
    检查是否需要触发 Dream，对标 claw-code 的触发条件：
    - 距上次整合超过 24 小时
    - 有 5+ 个新会话
    """
    store = MemoryStore()
    idx = store.get_index()

    # 检查时间条件
    if idx.last_consolidated_at:
        try:
            last = datetime.fromisoformat(idx.last_consolidated_at)
            hours_since = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if hours_since < DREAM_MIN_HOURS_SINCE_LAST:
                return False
        except Exception:
            pass

    # 检查新会话数量
    current_count = _count_sessions()
    new_sessions = current_count - idx.session_count_at_last_consolidation
    return new_sessions >= DREAM_MIN_NEW_SESSIONS


def run_dream(engine: "QueryEngine", verbose: bool = False) -> str:
    """
    四阶段记忆整合，对标 claw-code 的 Dream 流程。
    Orient → Gather → Consolidate → Prune

    engine: 用于调用 LLM 的 QueryEngine 实例（复用现有客户端）
    verbose: 是否打印过程日志
    """
    import uuid

    def log(msg: str):
        if verbose:
            print(f"[Dream] {msg}")

    store = MemoryStore()
    # 完整对话摘要（包含 user/assistant/工具调用/工具错误）
    session_full = _summarize_sessions()
    # Orient 阶段用较短版本（避免超 context），截断到约 8000 字符
    _ORIENT_MAX = 8_000
    session_orient = session_full[:_ORIENT_MAX] + ("…[已截断]" if len(session_full) > _ORIENT_MAX else "")
    log(f"开始 Dream，会话摘要长度: {len(session_full)} 字符")

    def ask_llm(prompt: str) -> str:
        """复用 engine 的 LLM 客户端，不走 tool use 循环"""
        resp = engine._client.chat.completions.create(
            model=engine.model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": "你是一个记忆整合助手，只输出 JSON，不输出其他内容。"},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content or ""

    def parse_json_safe(text: str) -> dict:
        import re
        # 提取 JSON 块
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        return {}

    # ── Phase 1: Orient ────────────────────────────────────────
    log("Phase 1: Orient")
    orient_raw = ask_llm(DREAM_PROMPTS["orient"].format(
        session_summaries=session_orient   # 用截断版，避免超 context
    ))
    orient_result = parse_json_safe(orient_raw)
    log(f"Orient 完成: {list(orient_result.keys())}")

    # ── Phase 2: Gather ────────────────────────────────────────
    log("Phase 2: Gather")
    gather_raw = ask_llm(DREAM_PROMPTS["gather"].format(
        orient_result=json.dumps(orient_result, ensure_ascii=False),
        session_content=session_full,     # 用完整版，提取细节
    ))
    gather_result = parse_json_safe(gather_raw)
    new_memories_data = gather_result.get("memories", [])
    log(f"Gather 完成: {len(new_memories_data)} 条新记忆")

    # ── Phase 3: Consolidate ───────────────────────────────────
    log("Phase 3: Consolidate")
    existing = store.load_all()
    existing_text = json.dumps(
        [{"id": m.id, "content": m.content, "tags": m.tags} for m in existing],
        ensure_ascii=False,
    )
    consolidate_raw = ask_llm(DREAM_PROMPTS["consolidate"].format(
        existing_memories=existing_text,
        new_memories=json.dumps(new_memories_data, ensure_ascii=False),
    ))
    consolidate_result = parse_json_safe(consolidate_raw)
    final_memories = consolidate_result.get("memories", new_memories_data)
    log(f"Consolidate 完成: {len(final_memories)} 条")

    # 保存整合后的新记忆
    saved_ids = []
    for item in final_memories:
        content = item.get("content", "").strip()
        if not content:
            continue
        mem_type = item.get("type", "project")
        if mem_type not in MEMORY_TYPES:
            mem_type = "project"
        mem = Memory(
            id=f"mem-{uuid.uuid4().hex[:8]}",
            content=content,
            tags=item.get("tags", []),
            created_at=datetime.now(timezone.utc).isoformat(),
            source_session="dream",
            importance=item.get("importance", 3),
            type=mem_type,
        )
        store.save(mem)
        saved_ids.append(mem.id)

    # ── Phase 4: Prune ─────────────────────────────────────────
    log("Phase 4: Prune")
    all_memories = store.load_all()
    mem_list_text = json.dumps(
        [{"id": m.id, "content": m.content, "importance": m.importance} for m in all_memories],
        ensure_ascii=False,
    )
    prune_raw = ask_llm(DREAM_PROMPTS["prune"].format(memories=mem_list_text))
    prune_result = parse_json_safe(prune_raw)

    # 更新重要度
    for item in prune_result.get("keep", []):
        path = MEMORY_DIR / f"{item['id']}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                data["importance"] = item.get("importance", data["importance"])
                path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            except Exception:
                pass

    # 删除低价值记忆
    deleted_count = 0
    for mem_id in prune_result.get("delete", []):
        if store.delete(mem_id):
            deleted_count += 1

    # 更新整合时间戳
    store.update_consolidation_timestamp(_count_sessions())

    summary = (
        f"Dream 完成：新增 {len(saved_ids)} 条记忆，"
        f"删除 {deleted_count} 条低价值记忆，"
        f"现有 {len(store.load_all())} 条记忆"
    )
    log(summary)
    return summary


def maybe_dream_in_background(engine: "QueryEngine") -> None:
    """
    检查是否需要 Dream，若需要则在后台线程运行。
    对标 claw-code 的后台自动触发逻辑。
    在 main.py 启动时调用。
    """
    if not should_dream():
        return

    def _dream_task():
        try:
            result = run_dream(engine, verbose=False)
            # 写入日志文件，不打扰前台
            log_file = MEMORY_DIR / "dream.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {result}\n")
        except Exception as e:
            log_file = MEMORY_DIR / "dream.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] Dream 失败: {e}\n")

    t = threading.Thread(target=_dream_task, name="shell-agent-dream", daemon=True)
    t.start()
