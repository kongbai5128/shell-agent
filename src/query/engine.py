from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field

from openai import OpenAI

from ..permissions import ToolPermissionContext
from ..sessions import StoredSession, new_session_id, save_session
from .. import tools

# ── 支持的 provider 配置 ──────────────────────────────────────
PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-opus-4-6",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o",
    },
}


def _detect_provider() -> str:
    """根据已设置的环境变量自动检测 provider"""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "deepseek"  # 默认

# ── Context window 大小（对标 claw-code utils/context.ts）────────
# 用于 auto-compact 阈值计算
_CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-chat":       64_000,
    "deepseek-reasoner":   64_000,
    "gpt-4o":             128_000,
    "gpt-4o-mini":        128_000,
    "gpt-4-turbo":        128_000,
    "claude-opus-4-6":    200_000,
    "claude-sonnet-4-5":  200_000,
    "claude-haiku-3-5":   200_000,
}
_DEFAULT_CONTEXT_WINDOW = 64_000

# 对标 claw-code AUTOCOMPACT_BUFFER_TOKENS = 13_000
# context_window - buffer = auto-compact 触发阈值
_AUTO_COMPACT_BUFFER = 13_000

# microcompact：超过此长度的旧 tool result 被截断（对标 Claude Code microCompact.ts）
_MICROCOMPACT_TOOL_RESULT_MAX = 500
# 最近 N 条消息不被 microcompact 处理（保留新鲜上下文）
_MICROCOMPACT_KEEP_RECENT = 10

# ── API 重试配置（对标 claw-code withRetry.ts）────────────────────
# DEFAULT_MAX_RETRIES = 10, BASE_DELAY_MS = 500, maxDelayMs = 32000
_MAX_API_RETRIES = 10
_BASE_RETRY_DELAY_MS = 500
_MAX_RETRY_DELAY_MS = 32_000

# 可重试的 HTTP 状态码：429 限流、529 过载、5xx 服务端错误
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504, 529})


def _parse_retry_after(exc: Exception) -> float | None:
    """从响应头解析 Retry-After 秒数。对标 claw-code getRetryAfter()。"""
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None)
        if headers:
            val = headers.get("retry-after")
            if val:
                return float(val)
    except Exception:
        pass
    return None


def _get_retry_delay(attempt: int, retry_after: float | None) -> float:
    """指数退避 + 25% 抖动，返回秒。对标 claw-code getRetryDelay()。

    delay = min(BASE * 2^(attempt-1), MAX) + jitter
    若响应头携带 Retry-After 则直接使用该值。
    """
    if retry_after is not None:
        return retry_after
    base = min(_BASE_RETRY_DELAY_MS * (2 ** (attempt - 1)), _MAX_RETRY_DELAY_MS)
    jitter = random.random() * 0.25 * base
    return (base + jitter) / 1000  # 转换为秒


@dataclass
class TurnResult:
    output: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    tool_calls: list[str] = field(default_factory=list)


@dataclass
class QueryEngine:
    """
    基于 OpenAI 兼容接口的 API 调用引擎，支持 DeepSeek / OpenAI / Anthropic。
    对标 claw-code 的 query_engine.py。
    """
    model: str = ""
    provider: str = ""
    permission_ctx: ToolPermissionContext = field(default_factory=ToolPermissionContext)
    session_id: str = field(default_factory=new_session_id)
    messages: list[dict] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    system_prompt: str = ""

    _client: OpenAI = field(init=False)

    def __post_init__(self):
        # 自动检测 provider
        if not self.provider:
            self.provider = _detect_provider()

        cfg = PROVIDERS.get(self.provider)
        if not cfg:
            raise ValueError(f"未知 provider: {self.provider}，可选: {list(PROVIDERS)}")

        api_key = os.environ.get(cfg["api_key_env"])
        if not api_key:
            raise EnvironmentError(
                f"未设置 {cfg['api_key_env']} 环境变量\n"
                f"请运行: export {cfg['api_key_env']}='your-key'"
            )

        # 默认模型
        if not self.model:
            self.model = cfg["default_model"]

        self._client = OpenAI(
            api_key=api_key,
            base_url=cfg["base_url"],
        )

    def run_turn(self, user_input: str, confirm_callback=None, tool_callback=None) -> TurnResult:
        """
        执行一轮对话，包含完整的 tool use 循环。
        confirm_callback: 危险工具执行前调用，返回 False 则跳过。
        tool_callback: 每次工具调用前触发，接收 (tool_name, tool_input)，用于实时显示进度。
        """
        # 自动 compact 检查（对标 claw-code autoCompact.ts shouldAutoCompact）
        # 在每轮开始前检查，超过阈值时先 microcompact，仍超则完整摘要压缩
        self._maybe_auto_compact(tool_callback)

        # 记录本轮开始前的消息数，异常时回滚，防止残缺 tool_calls 消息导致下轮 400 错误
        _rollback_len = len(self.messages)
        self.messages.append({"role": "user", "content": user_input})
        try:
            return self._run_turn_inner(user_input, confirm_callback, tool_callback)
        except BaseException:
            # 完整回滚本轮所有消息（user + assistant + tool results），保持历史合法
            self.messages = self.messages[:_rollback_len]
            raise

    def _run_turn_inner(self, user_input: str, confirm_callback=None, tool_callback=None) -> TurnResult:

        available_tools = [
            t for t in tools.all_tools()
            if not self.permission_ctx.blocks(t.name)
        ]
        # OpenAI 格式的工具定义
        tool_definitions = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in available_tools
        ]

        tool_calls_made = []

        # Tool use 循环
        while True:
            kwargs = dict(
                model=self.model,
                messages=[{"role": "system", "content": self.system_prompt}] + self.messages,
                tools=tool_definitions,
                tool_choice="auto",
                max_tokens=8096,
            )

            response = self._call_api_with_retry(tool_callback, **kwargs)
            choice = response.choices[0]
            msg = choice.message

            # 累计 token
            if response.usage:
                self.total_input_tokens += response.usage.prompt_tokens
                self.total_output_tokens += response.usage.completion_tokens
                in_tok = response.usage.prompt_tokens
                out_tok = response.usage.completion_tokens
            else:
                in_tok = out_tok = 0

            # 把 assistant 消息加入历史（序列化为 dict）
            msg_dict = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            self.messages.append(msg_dict)

            # 没有工具调用，结束
            if not msg.tool_calls:
                return TurnResult(
                    output=msg.content or "",
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    stop_reason=choice.finish_reason or "end_turn",
                    tool_calls=tool_calls_made,
                )

            # 有工具调用时，先显示模型的"思考文本"（若有）
            if msg.content and tool_callback:
                tool_callback("__thinking__", {"text": msg.content})

            # 处理工具调用
            import json
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_input = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                tool_calls_made.append(tool_name)

                if tool_callback:
                    tool_callback(tool_name, tool_input)

                if self.permission_ctx.blocks(tool_name):
                    result_content = f"权限拒绝：当前模式不允许使用工具 '{tool_name}'"
                elif self.permission_ctx.requires_confirm(tool_name) and confirm_callback:
                    confirmed = confirm_callback(tool_name, tool_input)
                    result_content = (
                        tools.execute(tool_name, tool_input)
                        if confirmed
                        else f"用户取消了工具 '{tool_name}' 的执行"
                    )
                else:
                    result_content = tools.execute(tool_name, tool_input)

                # 打印工具执行结果
                if tool_callback:
                    tool_callback("__result__", {"tool": tool_name, "result": result_content})

                # 工具结果用 OpenAI 格式返回
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_content,
                })

    def _call_api_with_retry(self, tool_callback=None, **kwargs):
        """
        带指数退避重试的 API 调用，对标 claw-code withRetry.ts。

        可重试条件：
          - 429 Rate Limit / 529 Overloaded / 5xx 服务端错误
          - 网络连接错误（ECONNRESET 等）
        不可重试：400 / 401 / 403 / 404 等客户端错误。
        """
        from openai import APIStatusError, APIConnectionError

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_API_RETRIES + 2):
            try:
                return self._client.chat.completions.create(**kwargs)
            except (APIStatusError, APIConnectionError) as exc:
                last_exc = exc

                # 判断是否可重试
                if isinstance(exc, APIStatusError):
                    if exc.status_code not in _RETRYABLE_STATUS_CODES:
                        raise  # 4xx 客户端错误不重试
                    retry_after = _parse_retry_after(exc)
                else:
                    # APIConnectionError（网络断开、连接重置）可重试
                    retry_after = None

                if attempt > _MAX_API_RETRIES:
                    raise  # 达到最大重试次数

                delay = _get_retry_delay(attempt, retry_after)
                if tool_callback:
                    tool_callback("__retry__", {
                        "attempt": attempt,
                        "max": _MAX_API_RETRIES,
                        "delay_s": round(delay, 1),
                        "error": str(exc),
                    })
                time.sleep(delay)

        # 理论上不会走到这里，但保险起见
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("API retry loop exited unexpectedly")

    def save(self) -> str:
        session = StoredSession(
            session_id=self.session_id,
            messages=self.messages,
            input_tokens=self.total_input_tokens,
            output_tokens=self.total_output_tokens,
            model=self.model,
        )
        save_session(session)
        return self.session_id

    def _estimate_tokens(self) -> int:
        """粗估当前 messages + system_prompt 的 token 数。

        对标 Claude Code roughTokenCountEstimation：
          总字符数 / 4（每 token 约 4 字符），再乘 4/3 保守系数。
        """
        total_chars = len(self.system_prompt)
        for m in self.messages:
            # role 本身
            total_chars += len(m.get("role", ""))
            # 文本内容
            content = m.get("content")
            if isinstance(content, str):
                total_chars += len(content)
            # tool_calls（assistant 消息里的工具调用参数）
            for tc in m.get("tool_calls", []):
                fn = tc.get("function", {})
                total_chars += len(fn.get("name", "")) + len(fn.get("arguments", ""))
        return int(total_chars / 4 * (4 / 3))

    def _microcompact(self) -> int:
        """截断旧 tool result 的内容，减少 token 但保留消息结构。

        对标 Claude Code microCompact.ts：
          只处理倒数第 _MICROCOMPACT_KEEP_RECENT 条之前的 tool 消息，
          把超长内容截断到 _MICROCOMPACT_TOOL_RESULT_MAX 字符。
          返回释放的估算 token 数。
        """
        freed_chars = 0
        cutoff = max(0, len(self.messages) - _MICROCOMPACT_KEEP_RECENT)
        for i in range(cutoff):
            m = self.messages[i]
            if m.get("role") == "tool":
                content = m.get("content", "")
                if isinstance(content, str) and len(content) > _MICROCOMPACT_TOOL_RESULT_MAX:
                    freed_chars += len(content) - _MICROCOMPACT_TOOL_RESULT_MAX
                    self.messages[i] = {
                        **m,
                        "content": content[:_MICROCOMPACT_TOOL_RESULT_MAX] + "\n…[旧结果已截断]",
                    }
        return int(freed_chars / 4)

    def _maybe_auto_compact(self, tool_callback=None) -> None:
        """若估算 token 数超过阈值，自动触发 compact。

        对标 Claude Code autoCompact.ts autoCompactIfNeeded：
          Step 1: microcompact（截断旧 tool result，成本低）
          Step 2: 若仍超阈值，执行完整摘要 compact
        通过 tool_callback 发送 __compact__ 事件通知 UI。
        """
        context_window = _CONTEXT_WINDOWS.get(self.model, _DEFAULT_CONTEXT_WINDOW)
        threshold = context_window - _AUTO_COMPACT_BUFFER
        if self._estimate_tokens() < threshold:
            return

        # Step 1: microcompact
        freed = self._microcompact()
        if tool_callback and freed > 0:
            tool_callback("__compact__", {"action": "microcompact", "freed_tokens": freed})

        # Step 2: 若仍超则完整摘要压缩
        if self._estimate_tokens() >= threshold:
            if tool_callback:
                tool_callback("__compact__", {"action": "full_compact"})
            self.compact()

    def compact(self) -> None:
        """压缩历史对话，节省 token。对标 claw-code /compact。

        先做 microcompact（截断旧 tool result），再整体摘要替换。
        改进原版：摘要 prompt 包含工具调用记录，不只是文本内容。
        """
        if len(self.messages) < 4:
            return

        # Step 1: microcompact（先截断旧 tool result，减少摘要输入量）
        self._microcompact()

        # Step 2: 把完整历史序列化为可读文本供摘要
        import json as _json
        parts = []
        for m in self.messages:
            role = m.get("role", "")
            content = m.get("content") or ""
            tool_calls = m.get("tool_calls", [])
            if tool_calls:
                calls_str = "; ".join(
                    f"{tc['function']['name']}({tc['function'].get('arguments', '')[:120]})"
                    for tc in tool_calls
                )
                parts.append(f"[{role}] 调用工具: {calls_str}")
                if content:
                    parts.append(f"[{role}] {content[:400]}")
            elif content:
                preview = content[:600]
                parts.append(f"[{role}] {preview}")

        history_text = "\n".join(parts)

        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": "你是一个对话摘要助手，擅长提炼技术对话的关键信息。"},
                {"role": "user", "content": (
                    "请对以下对话进行简洁摘要，需保留：\n"
                    "1. 用户的核心目标和需求\n"
                    "2. 已完成的重要操作（文件修改、命令执行等）\n"
                    "3. 关键技术决策和已知问题\n"
                    "4. 当前进展状态\n\n"
                    f"对话内容：\n{history_text}"
                )},
            ],
        )
        summary = resp.choices[0].message.content or ""
        self.messages = [
            {"role": "user", "content": f"[对话历史摘要]\n{summary}\n\n请基于以上历史继续。"},
            {"role": "assistant", "content": "已了解历史内容，请继续。"},
        ]
