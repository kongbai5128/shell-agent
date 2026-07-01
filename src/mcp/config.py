"""
MCP 配置加载和验证
对标 claw-code src/services/mcp/config.ts
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from ..paths import MCP_PROJECT_CONFIG
from .types import (
    MCPServerConfig,
    MCPStdioServerConfig,
    MCPSSEServerConfig,
    MCPWebSocketServerConfig,
    MCPHTTPServerConfig,
    MCPJsonConfig,
)

logger = logging.getLogger(__name__)


# 配置文件的典型位置
CONFIG_PATHS = [
    MCP_PROJECT_CONFIG,                              # 项目级配置
    Path.home() / ".shell-agent" / "mcp.json",       # 用户级配置
    Path.cwd() / "mcp.json",                          # 根目录配置
]


def parse_server_config(raw_config: Dict[str, Any]) -> Optional[MCPServerConfig]:
    """将原始配置字典转换为具体的服务器配置对象
    
    参数：
        raw_config: 从 JSON 读取的配置，应包含 "type" 字段
    
    示例：
        raw_config = {
            "type": "stdio",
            "command": "python",
            "args": ["weather_server.py"],
            "env": {"LOG_LEVEL": "DEBUG"}
        }
        config = parse_server_config(raw_config)
        # 返回 MCPStdioServerConfig(...)
    """
    server_type = raw_config.get("type", "stdio")
    
    try:
        if server_type == "stdio":
            return MCPStdioServerConfig(
                command=raw_config.get("command", ""),
                args=raw_config.get("args", []),
                env=raw_config.get("env", {}),
            )
        elif server_type == "sse":
            return MCPSSEServerConfig(
                url=raw_config.get("url", ""),
                headers=raw_config.get("headers", {}),
            )
        elif server_type == "ws":
            return MCPWebSocketServerConfig(
                url=raw_config.get("url", ""),
            )
        elif server_type == "http":
            return MCPHTTPServerConfig(
                url=raw_config.get("url", ""),
            )
        else:
            logger.warning(f"Unknown server type: {server_type}")
            return None
    
    except Exception as e:
        logger.error(f"Failed to parse server config: {e}")
        return None


def expand_env_vars(text: str) -> str:
    """展开环境变量和路径快捷方式
    
    支持：
    - $ENV_VAR 或 ${ENV_VAR} 格式
    - ~ 表示用户主目录
    - ~user 表示指定用户主目录
    
    示例：
        expand_env_vars("$HOME/.shell-agent/cache")  # /home/user/.shell-agent/cache
        expand_env_vars("~/data")                  # /home/user/data
    """
    import os
    import re
    
    # 展开环境变量
    def replace_env(match):
        var_name = match.group(1) or match.group(2)
        return os.environ.get(var_name, match.group(0))
    
    text = re.sub(r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)', replace_env, text)
    
    # 展开主目录
    text = os.path.expanduser(text)
    
    return text


def load_mcp_config(config_file: Optional[Path] = None) -> MCPJsonConfig:
    """加载 MCP 配置文件
    
    查找顺序：
    1. 明确指定的 config_file
    2. 项目级配置 (using/mcp.json)
    3. 用户级配置 (~/.shell-agent/mcp.json)
    4. 根目录配置 (./mcp.json)
    
    返回：
        MCPJsonConfig 实例，包含所有配置的服务器
    
    示例配置文件：
        {
            "weather": {
                "type": "stdio",
                "command": "python",
                "args": ["$PROJECT_ROOT/servers/weather_server.py"],
                "env": {
                    "CACHE_DIR": "~/.shell-agent/cache",
                    "LOG_LEVEL": "DEBUG"
                }
            },
            "location": {
                "type": "sse",
                "url": "https://api.example.com/mcp/location",
                "headers": {
                    "Authorization": "Bearer token-here"
                }
            }
        }
    """
    # 确定要读取的配置文件
    config_path = None
    
    if config_file and config_file.exists():
        config_path = config_file
    else:
        for path in CONFIG_PATHS:
            if path.exists():
                config_path = path
                logger.info(f"Found MCP config at: {config_path}")
                break
    
    if not config_path:
        logger.warning(
            f"No MCP config found in {', '.join(str(p) for p in CONFIG_PATHS)}. "
            "Using empty config."
        )
        return MCPJsonConfig()
    
    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
        
        # 验证并解析服务器配置
        servers = {}
        for server_id, server_config in raw_config.items():
            # 展开环境变量
            server_config = _expand_config_vars(server_config)
            
            parsed = parse_server_config(server_config)
            if parsed:
                servers[server_id] = parsed
                logger.debug(f"Loaded MCP server: {server_id}")
            else:
                logger.warning(f"Failed to parse MCP server config: {server_id}")
        
        return MCPJsonConfig(servers=servers)
    
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {config_path}: {e}")
        return MCPJsonConfig()
    except Exception as e:
        logger.error(f"Failed to load MCP config: {e}")
        return MCPJsonConfig()


def _expand_config_vars(config: Dict[str, Any]) -> Dict[str, Any]:
    """递归展开配置中的环境变量"""
    result = {}
    for key, value in config.items():
        if isinstance(value, str):
            result[key] = expand_env_vars(value)
        elif isinstance(value, dict):
            result[key] = _expand_config_vars(value)
        elif isinstance(value, list):
            result[key] = [
                expand_env_vars(v) if isinstance(v, str) else v
                for v in value
            ]
        else:
            result[key] = value
    return result


def save_mcp_config(config: MCPJsonConfig, config_file: Path) -> bool:
    """保存 MCP 配置到文件"""
    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 转换为可序列化的字典
        servers_dict = {}
        for server_id, server_config in config.servers.items():
            servers_dict[server_id] = {
                "type": server_config.type,
                **vars(server_config)
            }
        
        config_file.write_text(
            json.dumps(servers_dict, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        logger.info(f"Saved MCP config to: {config_file}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to save MCP config: {e}")
        return False


def ensure_default_config() -> Optional[Path]:
    """若配置文件不存在，创建含 GitHub MCP 服务器的默认配置。

    GitHub MCP 服务器通过 npx 运行，参考：
        https://www.modelscope.cn/mcp/servers/@modelcontextprotocol/github

    需要在环境变量中提前设置 GITHUB_PERSONAL_ACCESS_TOKEN，
    config.py 的 expand_env_vars 会在加载时自动展开 ${VAR} 占位符。

    返回已存在或刚创建的配置文件路径；若所有路径都已存在则返回第一个。
    """
    for path in CONFIG_PATHS:
        if path.exists():
            return path

    # 写到用户级配置目录 ~/.shell-agent/mcp.json
    config_file = Path.home() / ".shell-agent" / "mcp.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)

    default = {
        "github": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"
            }
        }
    }
    config_file.write_text(json.dumps(default, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Created default MCP config: {config_file}")
    return config_file
