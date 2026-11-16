"""
mcp_server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Model Context Protocol (MCP) Client SDK and Configuration Manager.
"""
from .client import MultiMCPClient
from .config import MCPConfigManager

__all__ = ["MultiMCPClient", "MCPConfigManager"]

