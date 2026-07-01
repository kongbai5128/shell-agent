# MCP (Model Context Protocol) integration for shell-agent
# 对标 claw-code src/services/mcp

from .types import (
    MCPServerConfig,
    MCPTool,
    MCPResource,
    TransportType,
)
from .client import MCPClient, MCPConnectionManager
from .config import load_mcp_config

__all__ = [
    'MCPServerConfig',
    'MCPTool',
    'MCPResource',
    'TransportType',
    'MCPClient',
    'MCPConnectionManager',
    'load_mcp_config',
]
