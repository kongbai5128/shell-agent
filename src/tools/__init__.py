from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolSpec:
    """一个工具的完整定义"""
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]
    dangerous: bool = False  # 是否需要执行前确认


# 全局工具注册表
_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> None:
    _REGISTRY[spec.name] = spec


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def all_tools() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def to_api_definitions() -> list[dict]:
    """转换为 Anthropic API 的 tools 格式"""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        }
        for spec in _REGISTRY.values()
    ]


def execute(name: str, input_data: dict) -> str:
    spec = get_tool(name)
    if spec is None:
        return f"错误：未知工具 '{name}'"
    try:
        return spec.handler(input_data)
    except Exception as e:
        return f"工具 '{name}' 执行出错: {e}"


# 导入所有工具，触发注册
from . import file_ops, shell, search, web, todo, skill_tool, mcp_tool, memory_tool  # noqa: E402, F401

# 浏览器工具（可选，需要 playwright）
try:
    from . import browser  # noqa: F401
except Exception:
    pass  # playwright 未安装时跳过，web_fetch 仍可用
