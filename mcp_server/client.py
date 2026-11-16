import asyncio
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional
from mcp import ClientSession

try:
    from mcp_server.config import MCPConfigManager
    from mcp_server.transports import connect_transport
except ImportError:
    from .config import MCPConfigManager
    from .transports import connect_transport


class MultiMCPClient:
    """
    A single unified client capable of connecting to multiple MCP servers defined
    in project_mcp.json and calling their tools.
    """
    def __init__(self, config_path: Optional[str] = None):
        self.config_manager = MCPConfigManager(config_path)
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stack: AsyncExitStack = AsyncExitStack()
        self._tool_cache: Dict[str, str] = {}  # tool_name -> server_name

    @property
    def servers_config(self) -> Dict[str, Any]:
        """Provides access to loaded server configurations."""
        return self.config_manager.servers_config

    def load_config(self) -> Dict[str, Any]:
        """Loads and parses project_mcp.json configuration."""
        return self.config_manager.load_config()

    async def connect_server(self, server_name: str) -> ClientSession:
        """Connects to a single MCP server by name."""
        if server_name in self.sessions:
            return self.sessions[server_name]

        config = self.config_manager.get_server_config(server_name)
        session = await connect_transport(server_name, config, self.exit_stack)
        self.sessions[server_name] = session
        return session

    async def connect(self, server_names: Optional[List[str]] = None):
        """Connects to specified servers (or all configured servers if None)."""
        if not self.servers_config:
            self.load_config()

        if server_names is None:
            server_names = list(self.servers_config.keys())

        print(f"Connecting to servers: {', '.join(server_names)}...")
        for name in server_names:
            try:
                await self.connect_server(name)
                print(f"  [+] Connected to '{name}' successfully.")
            except Exception as e:
                print(f"  [-] Failed to connect to '{name}': {e}")

        # Populate tool cache
        await self.refresh_tool_cache()

    async def refresh_tool_cache(self):
        """Fetches and caches tool mappings from all active server sessions."""
        self._tool_cache.clear()
        for server_name, session in self.sessions.items():
            try:
                tools_result = await session.list_tools()
                for tool in tools_result.tools:
                    self._tool_cache[tool.name] = server_name
            except Exception as e:
                print(f"Error listing tools for '{server_name}': {e}")

    async def call_tool(self, server_name: str, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Calls a specific tool on a designated server session."""
        if server_name not in self.sessions:
            await self.connect_server(server_name)
        
        session = self.sessions[server_name]
        return await session.call_tool(tool_name, arguments=arguments or {})

    async def list_prompts(self, server_name: str) -> Any:
        """Lists available prompts on a designated server session."""
        if server_name not in self.sessions:
            await self.connect_server(server_name)
        return await self.sessions[server_name].list_prompts()

    async def get_prompt(self, server_name: str, prompt_name: str, arguments: Optional[Dict[str, str]] = None) -> Any:
        """Gets a specific prompt on a designated server session."""
        if server_name not in self.sessions:
            await self.connect_server(server_name)
        return await self.sessions[server_name].get_prompt(prompt_name, arguments=arguments or {})

    async def list_resources(self, server_name: str) -> Any:
        """Lists available resources on a designated server session."""
        if server_name not in self.sessions:
            await self.connect_server(server_name)
        return await self.sessions[server_name].list_resources()

    async def read_resource(self, server_name: str, uri: str) -> Any:
        """Reads a specific resource by URI on a designated server session."""
        if server_name not in self.sessions:
            await self.connect_server(server_name)
        return await self.sessions[server_name].read_resource(uri)

    async def close(self):
        """Closes all active sessions and cleans up resources."""
        await self.exit_stack.aclose()
        # Give Windows proactor event loop time to flush pending SSL close frames cleanly
        await asyncio.sleep(0.25)
        self.sessions.clear()
        self._tool_cache.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
