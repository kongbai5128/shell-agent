from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PermissionMode(Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "full-access"


# 每种模式下，哪些工具被禁止
_MODE_DENY: dict[PermissionMode, list[str]] = {
    PermissionMode.READ_ONLY: ["write_file", "edit_file", "bash"],
    PermissionMode.WORKSPACE_WRITE: [],
    PermissionMode.FULL_ACCESS: [],
}

# 需要执行前询问用户确认的工具（full-access 模式下）
DANGEROUS_TOOLS = {"bash", "write_file", "edit_file"}


@dataclass(frozen=True)
class ToolPermissionContext:
    """直接借鉴自 claw-code src/permissions.py，扩展了 mode 支持"""
    mode: PermissionMode = PermissionMode.FULL_ACCESS
    deny_names: frozenset[str] = field(default_factory=frozenset)
    deny_prefixes: tuple[str, ...] = ()

    @classmethod
    def from_mode(cls, mode: PermissionMode) -> "ToolPermissionContext":
        deny = _MODE_DENY.get(mode, [])
        return cls(
            mode=mode,
            deny_names=frozenset(n.lower() for n in deny),
        )

    def blocks(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        return lowered in self.deny_names or any(
            lowered.startswith(p) for p in self.deny_prefixes
        )

    def requires_confirm(self, tool_name: str) -> bool:
        """full-access 模式下，危险工具执行前需要用户确认"""
        return (
            self.mode == PermissionMode.FULL_ACCESS
            and tool_name.lower() in DANGEROUS_TOOLS
        )
