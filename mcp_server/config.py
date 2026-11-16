import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
from dotenv import load_dotenv


class MCPConfigManager:
    """
    Manages loading, parsing, environment variable resolution, and adding new server
    configurations for MCP servers defined in project_mcp.json.
    """
    def __init__(self, config_path: Optional[str] = None):
        load_dotenv()
        if config_path is None:
            self.config_path = Path(__file__).parent / "project_mcp.json"
        else:
            self.config_path = Path(config_path)

        self.servers_config: Dict[str, Any] = {}

    def load_config(self) -> Dict[str, Any]:
        """Loads and parses project_mcp.json configuration."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.servers_config = data.get("mcpServers", {})
        return self.servers_config

    def _resolve_env_var(self, val: str) -> str:
        """Expand environment variable references like ${VAR_NAME} using os.environ."""
        def replace_match(match):
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))
        return re.sub(r"\$\{(\w+)\}", replace_match, val)

    def get_server_config(self, server_name: str) -> Dict[str, Any]:
        """Retrieves and resolves environment variables for a specific server configuration."""
        if not self.servers_config:
            self.load_config()

        if server_name not in self.servers_config:
            raise ValueError(f"Server '{server_name}' not found in configuration.")

        config = self.servers_config[server_name].copy()

        # If 'env' is present, resolve all references
        if "env" in config and isinstance(config["env"], dict):
            resolved_env = os.environ.copy()
            for k, v in config["env"].items():
                if isinstance(v, str):
                    resolved_env[k] = self._resolve_env_var(v)
                else:
                    resolved_env[k] = str(v)
            config["resolved_env"] = resolved_env

        return config
