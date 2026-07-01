"""
MCP 类型定义
对标 claw-code src/services/mcp/types.ts
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Dict, List
from enum import Enum


# ─── 传输类型 ───────────────────────────────────────────────────
TransportType = Literal["stdio", "sse", "http", "ws"]


# ─── 服务器配置 ─────────────────────────────────────────────────

@dataclass
class MCPStdioServerConfig:
    """本地 stdio 传输配置"""
    type: Literal["stdio"] = "stdio"
    command: str = ""  # 要运行的命令，如 "python weather_server.py"
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)  # 环境变量


@dataclass
class MCPSSEServerConfig:
    """远程 Server-Sent Events 传输配置"""
    type: Literal["sse"] = "sse"
    url: str = ""  # 服务器地址，如 "https://api.example.com/mcp"
    headers: Dict[str, str] = field(default_factory=dict)  # HTTP 请求头


@dataclass
class MCPWebSocketServerConfig:
    """WebSocket 传输配置"""
    type: Literal["ws"] = "ws"
    url: str = ""  # ws://... 或 wss://...


@dataclass
class MCPHTTPServerConfig:
    """HTTP 长连接配置"""
    type: Literal["http"] = "http"
    url: str = ""


# 统一的服务器配置类型
MCPServerConfig = (
    MCPStdioServerConfig | MCPSSEServerConfig | MCPWebSocketServerConfig | MCPHTTPServerConfig
)


# ─── MCP 协议中的工具和资源 ──────────────────────────────────────

@dataclass
class MCPToolInputSchema:
    """工具的输入参数 JSON Schema"""
    type: str = "object"
    properties: Dict[str, Any] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class MCPTool:
    """MCP 工具定义
    
    使用示例：
        MCPTool(
            name="get_weather",
            description="获取指定位置的天气预报",
            inputSchema=MCPToolInputSchema(
                properties={
                    "location": {"type": "string", "description": "城市名称"},
                    "days": {"type": "integer", "description": "预报天数"}
                },
                required=["location"]
            )
        )
    """
    name: str
    description: str
    inputSchema: MCPToolInputSchema = field(default_factory=MCPToolInputSchema)


@dataclass
class MCPResource:
    """MCP 资源定义（只读数据）"""
    uri: str  # 资源唯一标识，如 "weather://beijing"
    name: str  # 显示名称
    description: str = ""
    mimeType: str = "text/plain"
    contents: str = ""  # 资源内容


@dataclass
class MCPResourceTemplate:
    """资源模板（可动态生成资源）"""
    uri: str  # 模板 URI，支持 * 通配符，如 "weather://*"
    name: str
    description: str = ""
    mimeType: str = "text/plain"


@dataclass
class MCPServerCapabilities:
    """MCP 服务器提供的能力"""
    tools: List[MCPTool] = field(default_factory=list)
    resources: List[MCPResource] = field(default_factory=list)
    resourceTemplates: List[MCPResourceTemplate] = field(default_factory=list)
    prompts: List[Dict[str, Any]] = field(default_factory=list)  # 预定义提示词


@dataclass
class MCPCallToolRequest:
    """调用工具的请求"""
    toolName: str
    arguments: Dict[str, Any]


@dataclass
class MCPCallToolResult:
    """工具调用结果"""
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None


# ─── 连接和会话管理 ──────────────────────────────────────────────

@dataclass
class MCPServerConnection:
    """MCP 服务器连接状态"""
    serverId: str  # 服务器标识，如 "weather-mcp" 或 "location-mcp"
    config: MCPServerConfig
    connected: bool = False
    capabilities: Optional[MCPServerCapabilities] = None
    error: Optional[str] = None
    lastConnectedAt: Optional[str] = None


# ─── 配置文件结构 ────────────────────────────────────────────────

@dataclass
class MCPJsonConfig:
    """整个 MCP 配置文件结构（对应 settings.json 中的 mcp 字段）
    
    示例：
        {
            "weather": {
                "type": "stdio",
                "command": "python",
                "args": ["weather_server.py"],
                "env": {"CACHE_DIR": "~/.shell-agent/cache"}
            },
            "location": {
                "type": "sse",
                "url": "https://location-api.example.com/mcp"
            }
        }
    """
    servers: Dict[str, MCPServerConfig] = field(default_factory=dict)
