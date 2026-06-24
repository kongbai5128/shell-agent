"""
MCP 工具集成
允许 Claude 通过 MCP 调用远程和本地服务

对标 claw-code 中的工具集成机制
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from . import ToolSpec, register

logger = logging.getLogger(__name__)

# 全局 MCP 管理器（延迟初始化）
_mcp_manager: Optional[Any] = None


def _get_mcp_manager():
    """延迟初始化 MCP 管理器，避免启动时的性能开销"""
    global _mcp_manager
    if _mcp_manager is None:
        try:
            from ..mcp.client import MCPConnectionManager
            from ..mcp.config import load_mcp_config
            
            _mcp_manager = MCPConnectionManager()
            logger.debug("MCP manager initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize MCP manager: {e}")
            return None
    return _mcp_manager


def _mcp_call_tool(inp: dict) -> str:
    """调用 MCP 工具
    
    参数格式：
        {
            "server": "weather",          # MCP 服务器 ID
            "tool": "get_weather",        # 工具名称
            "arguments": {...}            # 工具参数
        }
    
    示例：
        {
            "server": "weather",
            "tool": "get_weather",
            "arguments": {"location": "北京", "days": 3}
        }
    """
    try:
        server_id: str = inp.get("server", "").strip()
        tool_name: str = inp.get("tool", "").strip()
        arguments: dict = inp.get("arguments", {})
        
        if not server_id:
            return "错误：必须指定 MCP 服务器 ID（'server' 字段）"
        if not tool_name:
            return "错误：必须指定工具名称（'tool' 字段）"
        
        manager = _get_mcp_manager()
        if not manager:
            return "错误：MCP 管理器未初始化"
        
        # 异步调用包装
        result = asyncio.run(
            manager.call_tool(server_id, tool_name, arguments)
        )
        
        if result.success:
            # 返回成功的工具输出
            return json.dumps(result.result, ensure_ascii=False, indent=2)
        else:
            return f"工具执行失败：{result.error}"
    
    except Exception as e:
        logger.error(f"MCP tool call failed: {e}")
        return f"MCP 工具调用异常：{str(e)}"


def _mcp_list_tools(inp: dict) -> str:
    """列出所有 MCP 服务器提供的工具
    
    用途：帮助 Claude 发现可用的 MCP 工具
    """
    try:
        manager = _get_mcp_manager()
        if not manager:
            return "错误：MCP 管理器未初始化，请检查配置"
        
        # 加载配置并连接所有服务器
        from ..mcp.config import load_mcp_config
        config = load_mcp_config()
        
        if not config.servers:
            return "未找到 MCP 配置，请创建 ~/.shell-agent/mcp.json"
        
        result = {
            "servers": {}
        }
        
        for server_id, tools in manager.list_all_tools().items():
            result["servers"][server_id] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.inputSchema.__dict__ if hasattr(t, 'inputSchema') else {}
                }
                for t in tools
            ]
        
        if not result["servers"]:
            result["message"] = "没有连接的 MCP 服务器，尚未初始化"
        
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    except Exception as e:
        logger.error(f"List MCP tools failed: {e}")
        return f"列出工具异常：{str(e)}"


# 注册 MCP 工具到工具系统
register(ToolSpec(
    name="mcp_call_tool",
    description=(
        "通过 MCP (Model Context Protocol) 调用已连接的 MCP 服务器上的工具。"
        "先使用 'mcp_list_tools' 查看可用的服务器和工具。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "MCP 服务器 ID，如 'github'",
                "examples": ["github"]
            },
            "tool": {
                "type": "string",
                "description": "要调用的工具名称，如 'search_repositories'",
                "examples": ["search_repositories", "create_repository"]
            },
            "arguments": {
                "type": "object",
                "description": "工具参数，具体内容取决于工具定义"
            }
        },
        "required": ["server", "tool"],
    },
    handler=_mcp_call_tool,
    dangerous=False,
))

register(ToolSpec(
    name="mcp_list_tools",
    description=(
        "列出所有可用的 MCP 工具。"
        "返回已连接的 MCP 服务器及其提供的工具列表、参数说明等。"
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": []
    },
    handler=_mcp_list_tools,
    dangerous=False,
))
