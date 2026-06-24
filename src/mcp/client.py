"""
MCP 客户端实现
对标 claw-code src/services/mcp/client.ts + MCPConnectionManager.tsx
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from pathlib import Path

from .types import (
    MCPServerConfig,
    MCPServerConnection,
    MCPCallToolRequest,
    MCPCallToolResult,
    MCPServerCapabilities,
    MCPStdioServerConfig,
    MCPSSEServerConfig,
    MCPTool,
)

logger = logging.getLogger(__name__)


@dataclass
class MCPClient:
    """单个 MCP 服务器的客户端连接
    
    职责：
    - 建立到 MCP 服务器的连接
    - 解析服务器公开的工具和资源
    - 转发工具调用请求并等待响应
    - 处理连接错误和重连
    """
    serverId: str
    config: MCPServerConfig
    
    # 运行时状态
    connected: bool = False
    process: Optional[subprocess.Popen] = None
    capabilities: Optional[MCPServerCapabilities] = None
    
    async def connect(self) -> bool:
        """建立连接，解析服务器能力
        
        支持两种连接方式：
        1. stdio: 启动本地进程并通过 stdin/stdout 通信
        2. sse: 连接到远程 HTTP 服务器
        """
        try:
            if isinstance(self.config, MCPStdioServerConfig):
                await self._connect_stdio()
            elif isinstance(self.config, MCPSSEServerConfig):
                await self._connect_sse()
            else:
                raise ValueError(f"Unsupported transport: {self.config.type}")
            
            self.connected = True
            await self._discover_capabilities()
            logger.info(f"Connected to MCP server: {self.serverId}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to {self.serverId}: {e}")
            self.connected = False
            return False
    
    async def _connect_stdio(self) -> None:
        """启动本地进程，通过 stdio 通信
        
        示例流程：
        1. subprocess.Popen("python weather_server.py")
        2. 服务器在 stdout 输出 JSON 行
        3. 发送工具调用请求到 stdout
        4. 接收 stdout 中的响应
        """
        if not isinstance(self.config, MCPStdioServerConfig):
            raise ValueError("Expected MCPStdioServerConfig")
        
        import os, re as _re
        cmd = [self.config.command] + self.config.args

        # 在子进程启动时（而非配置加载时）展开 ${VAR} 占位符，
        # 保证使用的是进程启动那一刻最新的 os.environ。
        # config.py 的 expand_env_vars 在加载时若变量不存在会保留原始占位符，
        # 这里做二次展开兜底，并对仍未展开的变量发出警告。
        def _expand_at_launch(val: str) -> str:
            def _sub(m):
                name = m.group(1)
                v = os.environ.get(name, "")
                if not v:
                    logger.warning(
                        f"[{self.serverId}] env var ${{{name}}} is not set. "
                        f"Set it with: export {name}=<your_token>"
                    )
                return v
            return _re.sub(r"\$\{([^}]+)\}", _sub, val)

        base_env = {**os.environ}
        for k, v in (self.config.env or {}).items():
            base_env[k] = _expand_at_launch(v)
        env = base_env
        
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        # 关键：用后台 daemon 线程持续消费 stderr。
        # 原因：OS 给每个管道分配约 64 KB 缓冲区。若不读取 stderr，
        # npx 进程（安装日志、运行日志均写 stderr）会在缓冲区满后
        # 阻塞在 write(stderr)，无法再处理 stdin 请求，
        # 导致我们写 stdin 时对端管道已关闭 → Broken pipe (EPIPE)。
        def _drain_stderr():
            for line in self.process.stderr:
                logger.debug(f"[{self.serverId} stderr] {line.rstrip()}")
        threading.Thread(target=_drain_stderr, daemon=True, name=f"mcp-stderr-{self.serverId}").start()

        logger.debug(f"Started stdio MCP server: {' '.join(cmd)}")
    
    async def _connect_sse(self) -> None:
        """连接到远程 SSE 服务器（aiohttp/httpx 未安装时仅记录参数）"""
        if not isinstance(self.config, MCPSSEServerConfig):
            raise ValueError("Expected MCPSSEServerConfig")
        logger.debug(f"Connecting to SSE server: {self.config.url}")
        # TODO: 使用 aiohttp / httpx 实现真实的 SSE 长连接

    @staticmethod
    def _parse_tool(t: dict) -> "MCPTool":
        """将服务器返回的工具定义字典转换为 MCPTool dataclass

        标准 MCP servers（如 @modelcontextprotocol/server-github）返回格式：
            {
                "name": "search_repositories",
                "description": "Search for GitHub repositories",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]
                }
            }
        inputSchema 是原始 dict，不能直接用 MCPTool(**t)，需先转为 MCPToolInputSchema。
        """
        from .types import MCPToolInputSchema
        schema = t.get("inputSchema", {})
        if isinstance(schema, dict):
            schema = MCPToolInputSchema(
                type=schema.get("type", "object"),
                properties=schema.get("properties", {}),
                required=schema.get("required", []),
                description=schema.get("description", ""),
            )
        return MCPTool(
            name=t["name"],
            description=t.get("description", ""),
            inputSchema=schema,
        )

    async def _send_notification_stdio(self, method: str) -> None:
        """向 stdio 服务器发送 JSON-RPC 通知（无 id，不等待响应）"""
        if not self.process:
            return
        notification = {"jsonrpc": "2.0", "method": method}
        self.process.stdin.write(json.dumps(notification) + "\n")
        self.process.stdin.flush()

    async def _discover_capabilities(self) -> None:
        """执行标准 MCP 握手流程并获取工具列表

        标准三步协议（对标 claw-code src/services/mcp/client.ts）：

        Step 1 — initialize (request/response)
            客户端声明协议版本 → 服务器返回 serverInfo 和 capabilities。
            注意：capabilities 只表示支持哪些功能，不包含具体工具列表；
            例如 GitHub MCP 返回 {"tools": {"listChanged": true}}。

        Step 2 — notifications/initialized (notification，服务器不回复)
            告知服务器客户端初始化已完成，服务器才开始处理业务请求。

        Step 3 — tools/list (request/response)
            获取完整工具列表，每个工具含 name、description、inputSchema。
        """
        try:
            # Step 1: initialize
            init_resp = await self.call_raw({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "shell-agent", "version": "0.1.0"},
                    "capabilities": {}
                }
            })
            if not init_resp or "error" in init_resp:
                raise RuntimeError(f"initialize failed: {init_resp}")

            # Step 2: notifications/initialized（通知，服务器不回复）
            if isinstance(self.config, MCPStdioServerConfig):
                await self._send_notification_stdio("notifications/initialized")

            # Step 3: tools/list
            tools_resp = await self.call_raw({
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
            })
            if tools_resp and "result" in tools_resp:
                tools_raw = tools_resp["result"].get("tools", [])
                self.capabilities = MCPServerCapabilities(
                    tools=[self._parse_tool(t) for t in tools_raw],
                )
                logger.info(f"{self.serverId}: {len(self.capabilities.tools)} tools discovered")
            else:
                logger.warning(f"{self.serverId}: tools/list returned no result")
                self.capabilities = MCPServerCapabilities()

        except Exception as e:
            logger.error(f"Failed to discover capabilities for {self.serverId}: {e}")
            self.capabilities = MCPServerCapabilities()
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> MCPCallToolResult:
        """调用服务器上的工具
        
        参数：
            tool_name: 工具名称，如 "get_weather"
            arguments: 工具参数，如 {"location": "北京"}
        
        返回：
            成功时 result 包含工具输出
            失败时 error 包含错误信息
        
        示例：
            result = await client.call_tool("get_weather", {"location": "北京"})
            if result.success:
                print(result.result)
            else:
                print(f"Error: {result.error}")
        """
        try:
            request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            }
            
            response = await self.call_raw(request)
            
            if response and "result" in response:
                return MCPCallToolResult(
                    success=True,
                    result=response["result"],
                )
            elif response and "error" in response:
                return MCPCallToolResult(
                    success=False,
                    error=response["error"].get("message", "Unknown error"),
                )
            else:
                return MCPCallToolResult(
                    success=False,
                    error="Empty response from server",
                )
        
        except Exception as e:
            logger.error(f"Tool call failed: {e}")
            return MCPCallToolResult(success=False, error=str(e))
    
    async def call_raw(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """发送原始 JSON-RPC 请求
        
        这是底层通信方法，具体实现取决于传输类型。
        """
        if isinstance(self.config, MCPStdioServerConfig):
            return await self._call_raw_stdio(request)
        elif isinstance(self.config, MCPSSEServerConfig):
            return await self._call_raw_sse(request)
        else:
            raise ValueError(f"Unsupported transport: {self.config.type}")
    
    async def _call_raw_stdio(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """通过 stdio 发送请求并读取第一行合法 JSON 响应。

        npx 首次运行会在 stdout 输出安装进度（如 "added 42 packages"），
        这些行不是 JSON。逐行读取直到遇到 '{' 开头的行，
        其余行记录为 debug 日志并跳过。
        同时在写入前检查进程是否仍然存活。
        """
        if not self.process or self.process.poll() is not None:
            raise RuntimeError(f"Process for {self.serverId!r} is not running (exit code: {self.process.poll() if self.process else 'N/A'})")

        try:
            request_line = json.dumps(request, ensure_ascii=False) + "\n"
            self.process.stdin.write(request_line)
            self.process.stdin.flush()

            # 跳过 npx 安装输出等非 JSON 行，只取第一行以 '{' 开头的行
            while True:
                response_line = self.process.stdout.readline()
                if not response_line:
                    # EOF：进程已退出
                    return None
                response_line = response_line.strip()
                if response_line.startswith("{"):
                    return json.loads(response_line)
                logger.debug(f"[{self.serverId}] skipping non-JSON stdout: {response_line[:120]}")

        except Exception as e:
            logger.error(f"stdio communication error: {e}")
            return None
    
    async def _call_raw_sse(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """通过 SSE/HTTP 发送请求"""
        # TODO: 实现 HTTP POST 请求并解析 SSE 响应
        logger.warning("SSE transport not fully implemented yet")
        return None
    
    async def disconnect(self) -> None:
        """断开连接"""
        try:
            if self.process:
                self.process.terminate()
                self.process.wait(timeout=5)
        except Exception as e:
            logger.error(f"Error during disconnect: {e}")
        finally:
            self.connected = False
            self.process = None
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


@dataclass
class MCPConnectionManager:
    """管理多个 MCP 服务器连接
    
    职责：
    - 维护多个 MCPClient 实例
    - 提供统一的工具调用接口
    - 处理连接池和重连逻辑
    
    使用示例：
        manager = MCPConnectionManager()
        
        # 添加服务器配置
        await manager.add_server("weather", MCPStdioServerConfig(
            command="python",
            args=["weather_server.py"]
        ))
        
        # 调用工具
        result = await manager.call_tool("weather", "get_weather", 
                                         {"location": "北京"})
    """
    clients: Dict[str, MCPClient] = field(default_factory=dict)
    
    async def add_server(self, server_id: str, config: MCPServerConfig) -> bool:
        """添加并连接一个 MCP 服务器"""
        client = MCPClient(server_id, config)
        success = await client.connect()
        if success:
            self.clients[server_id] = client
        return success
    
    async def call_tool(self, server_id: str, tool_name: str, 
                        arguments: Dict[str, Any]) -> MCPCallToolResult:
        """从指定服务器调用工具"""
        if server_id not in self.clients:
            return MCPCallToolResult(
                success=False,
                error=f"Server not connected: {server_id}"
            )
        
        client = self.clients[server_id]
        return await client.call_tool(tool_name, arguments)
    
    def get_server_capabilities(self, server_id: str) -> Optional[MCPServerCapabilities]:
        """获取服务器的能力描述"""
        if server_id not in self.clients:
            return None
        return self.clients[server_id].capabilities
    
    def list_all_tools(self) -> Dict[str, List[MCPTool]]:
        """列出所有连接的服务器提供的工具"""
        result = {}
        for server_id, client in self.clients.items():
            if client.capabilities:
                result[server_id] = client.capabilities.tools
        return result
    
    async def disconnect_all(self) -> None:
        """断开所有连接"""
        for client in self.clients.values():
            await client.disconnect()
        self.clients.clear()
