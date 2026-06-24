from __future__ import annotations

import subprocess
import shlex

from . import ToolSpec, register

# ── 安全沙盒：对标 claw-code bash.rs 的黑名单设计 ─────────────

# 绝对禁止的命令前缀（无论任何权限模式）
_BLOCKED_PATTERNS: list[str] = [
    "rm -rf /",
    "rm -rf ~",
    ":(){ :|:& };:",       # fork bomb
    "dd if=/dev/zero of=/dev/",
    "mkfs",
    "> /dev/sda",
    "chmod -R 777 /",
    "curl | bash",
    "curl | sh",
    "wget | bash",
    "wget | sh",
]

# 需要额外警告但不阻止的关键词
_WARN_PATTERNS: list[str] = [
    "sudo",
    "su ",
    "chmod 777",
    "chown",
    "kill -9",
    "> /etc/",
    "passwd",
]

MAX_OUTPUT_CHARS = 10_000  # 对标 claw-code 的输出截断


def _check_command_safety(cmd: str) -> str | None:
    """
    检查命令是否包含危险模式。
    返回 None 表示安全，返回字符串表示拦截原因。
    对标 claw-code sandbox.rs 的检查逻辑。
    """
    cmd_lower = cmd.lower().strip()
    for pattern in _BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return f"命令包含危险模式 '{pattern}'，已拒绝执行"
    return None


def _bash(inp: dict) -> str:
    cmd = inp["command"]
    timeout = inp.get("timeout", 30)
    description = inp.get("description", "")  # 对标 claw-code BashCommandInput.description

    # 安全检查
    block_reason = _check_command_safety(cmd)
    if block_reason:
        return f"🚫 安全拦截：{block_reason}"

    # 警告提示
    warnings = []
    for pattern in _WARN_PATTERNS:
        if pattern in cmd.lower():
            warnings.append(pattern)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output_parts = []

        if warnings:
            output_parts.append(f"⚠️  包含敏感操作: {', '.join(warnings)}")

        if result.stdout:
            stdout = result.stdout.rstrip()
            # 输出截断，对标 claw-code 的 persisted_output_size 逻辑
            if len(stdout) > MAX_OUTPUT_CHARS:
                stdout = stdout[:MAX_OUTPUT_CHARS] + f"\n... [输出已截断，共 {len(result.stdout)} 字符]"
            output_parts.append(f"stdout:\n{stdout}")

        if result.stderr:
            stderr = result.stderr.rstrip()
            if len(stderr) > MAX_OUTPUT_CHARS:
                stderr = stderr[:MAX_OUTPUT_CHARS] + "\n... [stderr 已截断]"
            output_parts.append(f"stderr:\n{stderr}")

        output_parts.append(f"exit code: {result.returncode}")
        return "\n".join(output_parts) if output_parts else "(无输出)"

    except subprocess.TimeoutExpired:
        return f"错误：命令超时（{timeout}秒）\n对标 claw-code: interrupted=true"
    except Exception as e:
        return f"执行失败: {e}"


register(ToolSpec(
    name="bash",
    description=(
        "在 shell 中执行命令，返回 stdout、stderr 和退出码。"
        "内置安全检查，拒绝危险命令（rm -rf /、fork bomb 等）。"
        "输出超过 10000 字符时自动截断。"
        "对标 claw-code bash 工具。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 30",
                "default": 30,
            },
            "description": {
                "type": "string",
                "description": "命令用途说明（便于用户理解意图）",
            },
        },
        "required": ["command"],
    },
    handler=_bash,
    dangerous=True,
))
