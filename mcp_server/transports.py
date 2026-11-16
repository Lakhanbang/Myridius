import os
import shutil
from contextlib import AsyncExitStack
from typing import Any, Dict

import httpx
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client


async def connect_transport(server_name: str, config: Dict[str, Any], exit_stack: AsyncExitStack) -> ClientSession:
    """
    Given a resolved server config dict and an AsyncExitStack, establishes the connection
    (HTTP/Streamable HTTP or Stdio subprocess) and returns an initialized ClientSession.
    """
    if "url" in config:
        url = config["url"]
        headers = config.get("headers", {})
        http_client = await exit_stack.enter_async_context(
            httpx.AsyncClient(headers=headers)
        )
        read_stream, write_stream, _ = await exit_stack.enter_async_context(
            streamable_http_client(url, http_client=http_client, terminate_on_close=False)
        )
        session = await exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        return session

    elif "command" in config:
        command = config["command"]
        args = list(config.get("args", []))
        
        # On Windows, bypass cmd.exe ampersand splitting (&) by running node.exe directly with npx-cli.js
        if os.name == "nt" and command.lower() in ("npx", "npx.cmd"):
            node_path = shutil.which("node") or "node"
            npx_cli_candidates = [
                r"C:\Program Files\nodejs\node_modules\npm\bin\npx-cli.js",
                os.path.join(os.path.dirname(shutil.which("node") or ""), "node_modules", "npm", "bin", "npx-cli.js"),
                os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules", "npm", "bin", "npx-cli.js")
            ]
            for candidate in npx_cli_candidates:
                if candidate and os.path.exists(candidate):
                    command = node_path
                    args = [candidate] + args
                    break

        # Use resolved_env if prepared by config manager, otherwise os.environ
        full_env = config.get("resolved_env", os.environ.copy())

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=full_env
        )
        read_stream, write_stream = await exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        session = await exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        return session

    else:
        raise ValueError(f"Invalid server configuration for '{server_name}': must have 'command' or 'url'")
