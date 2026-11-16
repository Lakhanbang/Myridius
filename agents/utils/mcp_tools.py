"""
mcp_tools.py
───────────────────────────────────────────────────────────────────────────
Pattern 1 — Universal MCP Meta-Tools for CrewAI subagents.

Provides a shared MultiMCPClient singleton (with a dedicated asyncio event
loop running in a daemon thread) and two CrewAI BaseTool wrappers:

  1. SearchMCPTools  — discover tool names & schemas across all servers
  2. CallMCPTool     — execute any discovered tool by name + JSON args

them full access to all MCP servers (filesystem, google, qdrant, mcp-document-reader, prompts)
alongside their existing domain-specific tools.
───────────────────────────────────────────────────────────────────────────
"""

import asyncio
import json
import logging
import os
import threading
from typing import Any, Dict, Optional, Union, Type
from pydantic import BaseModel, Field

from crewai.tools import BaseTool

from mcp_server.client import MultiMCPClient
from agents.utils.tool_helpers import safe_tool_run

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Shared singleton — one MultiMCPClient + one background asyncio loop
# ─────────────────────────────────────────────────────────────────────────────

_mcp_client: Optional[MultiMCPClient] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_lock = threading.Lock()


def _start_background_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Target for the daemon thread that runs the asyncio event loop."""
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Returns the shared background asyncio event loop, starting it if needed."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        thread = threading.Thread(target=_start_background_loop, args=(_loop,), daemon=True)
        thread.start()
    return _loop


def _run_async(coro) -> Any:
    """Submit a coroutine to the background loop and block until it completes."""
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=300)


def get_shared_mcp_client() -> MultiMCPClient:
    """
    Returns the singleton MultiMCPClient instance.
    Thread-safe; creates the client on first call but does NOT connect.
    Call ensure_mcp_connected() to establish server connections.
    """
    global _mcp_client
    with _lock:
        if _mcp_client is None:
            _mcp_client = MultiMCPClient()
        return _mcp_client


def ensure_mcp_connected() -> None:
    """
    Connects the shared MultiMCPClient to all configured servers.
    Safe to call multiple times — skips if already connected.
    """
    client = get_shared_mcp_client()
    if client.sessions:
        return  # Already connected
    _run_async(client.connect())


# ─────────────────────────────────────────────────────────────────────────────
# CrewAI Tool 1 — Search MCP Tools
# ─────────────────────────────────────────────────────────────────────────────

class SearchMCPToolsSchema(BaseModel):
    query: str = Field(..., description="A simple text string to search for tools by keyword (e.g. 'read file', 'google drive'). Do NOT pass a JSON object or dictionary.")

class SearchMCPTools(BaseTool):
    name: str = "search_mcp_tools"
    description: str = (
        "DO NOT USE FOR: Searching the public internet, looking up news, current events, or general software/API updates. "
        "If you need public internet information or current updates, ALWAYS call the 'Web Search' tool instead of search_mcp_tools.\n"
        "VERBATIM LAW: Exactly report only verified tool names found by this search."
    )
    args_schema: type[BaseModel] = SearchMCPToolsSchema
    allowed_servers: Optional[list] = Field(None, exclude=True)

    @safe_tool_run("search_mcp_tools")
    def _run(self, query: str) -> str:
        client = get_shared_mcp_client()
        if not client.sessions:
            return "Error: MCP servers are not connected. Cannot search tools."

        async def _search():
            q_lower = query.lower().strip()
            import re
            q_words = [w for w in re.findall(r'\b[a-z0-9]{3,}\b', q_lower) if w not in ("the", "and", "for", "with", "from", "using", "into", "all", "this", "that", "have", "are", "was", "can", "you", "please")]
            scored = []
            for srv_name, session in client.sessions.items():
                if self.allowed_servers is not None and srv_name not in self.allowed_servers:
                    continue
                tools_res = await session.list_tools()
                for t in tools_res.tools:
                    t_name_clean = t.name.lower().replace("_", " ")
                    t_desc_clean = (t.description or "").lower()
                    
                    # Calculate keyword relevance score across server name, tool name, and description
                    score = 0
                    if q_lower in srv_name.lower(): score += 10
                    if q_lower in t.name.lower(): score += 15
                    if q_lower in t_desc_clean: score += 5
                    for w in q_words:
                        if w in srv_name.lower(): score += 4
                        if w in t_name_clean: score += 4
                        if w in t_desc_clean: score += 1
                    
                    # Boost essential primary data retrieval action tools so list_docs, readDocument, list_directory rank #1
                    if any(verb in t.name.lower() for verb in ("list_", "readdocument", "read_file", "search_", "get_", "execute_")):
                        score += 25
                    elif any(verb in t.name.lower() for verb in ("list", "read", "search", "get", "query", "fetch")):
                        score += 15
                        
                    # Penalize obscure formatting/styling/insert tools unless explicitly requested
                    if any(obscure in t.name.lower() for obscure in ("insertimage", "insertrichlink", "replacetextstyle", "format", "style", "margin", "color", "border")):
                        if not any(req in q_lower for req in ("image", "link", "style", "color", "format", "border")):
                            score -= 20
                    
                    if q_lower in ("all", "*", "", "list"):
                        score = 1 if not any(verb in t.name.lower() for verb in ("list_", "read", "search")) else 10
                    
                    scored.append((score, {
                        "server": srv_name,
                        "tool_name": t.name,
                        "description": t.description or "",
                        "parameters": getattr(t, "inputSchema", {}),
                    }))
            
            # Sort descending by relevance score
            scored.sort(key=lambda x: x[0], reverse=True)
            matches = [item[1] for item in scored if item[0] > 0]
            
            # If no direct keyword hits AND query is wildcard, return top tools. Otherwise intercept with Traffic Shield!
            if not matches:
                if q_lower in ("all", "*", "", "list") and scored:
                    matches = [item[1] for item in scored[:15]]
                else:
                    return (
                        f"[REDIRECT DIRECTIVE]: No internal MCP tools found matching query: '{query}'.\n"
                        f"You are attempting to search for external/public information, news, current events, or general knowledge using internal system tools.\n"
                        f"STOP calling search_mcp_tools immediately. You MUST call the 'Web Search' tool (`search_tool` / `FallbackSearchTool`) right now to search the live internet."
                    )
                
            return (
                f"Found {len(matches)} matching MCP tool(s):\n"
                + json.dumps(matches[:15], indent=2)
                + "\n\nCRITICAL MANDATORY NEXT STEP: You MUST now execute 'Action: call_mcp_tool' passing tool_name and arguments_json directly. Format: call_mcp_tool(tool_name='searchDriveFiles', arguments_json='{\"query\": \"mcp.txt\"}') or 'searchDriveFiles | {\"query\": \"mcp.txt\"}'. DO NOT output 'Final Answer:' or guess placeholder data (`file1.txt`, `mock_data`) under ANY CIRCUMSTANCES until you have called call_mcp_tool and received real output!"
            )

        return _run_async(_search())


# ─────────────────────────────────────────────────────────────────────────────
# CrewAI Tool 2 — Call MCP Tool
# ─────────────────────────────────────────────────────────────────────────────

# Dynamically resolve root project folder (dev_incubator/r&d) without hardcoding absolute OS path
_WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class CallMCPToolSchema(BaseModel):
    tool_name: Optional[str] = Field(None, description="Exact name of the MCP tool to call (e.g. 'google_searchDriveFiles', 'create_issue', 'read_file'). Required if not using query pipe format.")
    arguments_json: Optional[Union[str, dict]] = Field(None, description="JSON string or dictionary containing required arguments for the tool (e.g. '{\"query\": \"resume\"}').")
    query: Optional[str] = Field(None, description="Legacy pipe format: '<tool_name> | <arguments_json>' (e.g. 'searchDriveFiles | {\"query\": \"mcp.txt\"}').")


class CallMCPTool(BaseTool):
    name: str = "call_mcp_tool"
    description: str = (
        "Execute any MCP tool discovered via search_mcp_tools. "
        "Input format: pass tool_name and arguments_json directly (e.g. call_mcp_tool(tool_name='searchDriveFiles', arguments_json='{\"query\": \"mcp.txt\"}')) "
        "or pipe format: '<tool_name> | <arguments_json>'. "
        "The tool_name must be the exact name from search_mcp_tools results.\n"
        "SELF-HEALING LAW: If the tool returns an Error (`File not found`), DO NOT guess contents. Record it under `Skipped unverified/missing items`. Mirror exact text verbatim."
    )
    args_schema: type[BaseModel] = CallMCPToolSchema
    allowed_servers: Optional[list] = Field(None, exclude=True)

    @safe_tool_run("call_mcp_tool")
    def _run(self, query: Optional[str] = None, tool_name: Optional[str] = None, arguments_json: Optional[Union[str, dict]] = None, **kwargs) -> str:
        # Check if kwargs has tool_name / arguments_json if CrewAI passed extra parameters
        tool_name = tool_name or kwargs.get("tool_name") or kwargs.get("name")
        arguments_json = arguments_json or kwargs.get("arguments_json") or kwargs.get("arguments") or kwargs.get("args")
        if not tool_name and not query and kwargs:
            tool_name = kwargs.pop("tool_name", None) or kwargs.pop("tool", None) or kwargs.pop("name", None)
            if tool_name and not arguments_json:
                arguments_json = kwargs

        args_dict = {}
        if tool_name:
            tool_name = tool_name.strip()
            if isinstance(arguments_json, dict):
                args_dict = arguments_json
            elif isinstance(arguments_json, str) and arguments_json.strip():
                try:
                    args_dict = json.loads(arguments_json.strip())
                except json.JSONDecodeError as e:
                    return f"Error: Invalid JSON in arguments_json: {e}. Please provide valid JSON."
        elif query:
            if "|" not in query:
                # If query itself is valid JSON containing tool_name or arguments
                try:
                    parsed = json.loads(query.strip())
                    if isinstance(parsed, dict) and ("tool_name" in parsed or "name" in parsed):
                        tool_name = parsed.get("tool_name") or parsed.get("name")
                        raw_args = parsed.get("arguments_json") or parsed.get("arguments") or parsed.get("args") or {}
                        args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    else:
                        return (
                            "Error: Input must be formatted as '<tool_name> | <arguments_json>' or passed as separate tool_name and arguments_json parameters. "
                            "Example: call_mcp_tool(tool_name='searchDriveFiles', arguments_json='{\"query\": \"mcp.txt\"}')"
                        )
                except Exception:
                    return (
                        "Error: Input must be formatted as '<tool_name> | <arguments_json>' or passed as separate tool_name and arguments_json parameters. "
                        "Example: call_mcp_tool(tool_name='searchDriveFiles', arguments_json='{\"query\": \"mcp.txt\"}')"
                    )
            else:
                tool_name, args_raw = query.split("|", 1)
                tool_name = tool_name.strip()
                args_raw = args_raw.strip()
                try:
                    args_dict = json.loads(args_raw) if args_raw else {}
                except json.JSONDecodeError as e:
                    return f"Error: Invalid JSON in arguments: {e}. Please provide valid JSON."
        else:
            return "Error: You must provide tool_name and arguments_json (e.g. call_mcp_tool(tool_name='searchDriveFiles', arguments_json='{\"query\": \"mcp.txt\"}'))."

        client = get_shared_mcp_client()
        if not client.sessions:
            return "Error: MCP servers are not connected."

        async def _call():
            async def _execute(resolved_name: str, server_name: str, args: dict) -> str:
                res = await client.call_tool(server_name, resolved_name, args)
                if hasattr(res, "content") and res.content:
                    parts = []
                    for item in res.content:
                        if hasattr(item, "text") and item.text:
                            parts.append(item.text)
                        else:
                            parts.append(str(item))
                    output = "\\n".join(parts)
                else:
                    output = str(res)
                # Truncate very large outputs to protect context window
                if len(output) > 80000:
                    output = output[:80000] + "\\n[... Output truncated ...]"
                return output

            # Resolve server for the tool
            server_name = client._tool_cache.get(tool_name)
            if not server_name:
                await client.refresh_tool_cache()
                server_name = client._tool_cache.get(tool_name)
                # Case-insensitive fallback
                if not server_name:
                    for k, v in client._tool_cache.items():
                        if k.lower() == tool_name.lower():
                            server_name = v
                            tool_name_resolved = k
                            if self.allowed_servers is not None and server_name not in self.allowed_servers:
                                return f"Error: Access denied. Server '{server_name}' is not in your allowed MCP servers ({self.allowed_servers})."
                            return await _execute(tool_name_resolved, server_name, args_dict)
                    return (
                        f"Error: Tool '{tool_name}' not found across connected servers. "
                        "Run search_mcp_tools first to discover available tools."
                    )
            if self.allowed_servers is not None and server_name not in self.allowed_servers:
                return f"Error: Access denied. Server '{server_name}' is not in your allowed MCP servers ({self.allowed_servers})."
            return await _execute(tool_name, server_name, args_dict)

        return _run_async(_call())


# ─────────────────────────────────────────────────────────────────────────────
# CrewAI Tool 3 — Search MCP Prompts
# ─────────────────────────────────────────────────────────────────────────────

class SearchMCPPrompts(BaseTool):
    name: str = "search_mcp_prompts"
    description: str = (
        "USE THIS FOR: Discovering available prompts and prompt templates provided by connected MCP servers. "
        "Returns prompt names, descriptions, and required arguments."
    )
    allowed_servers: Optional[list] = Field(None, exclude=True)

    @safe_tool_run("search_mcp_prompts")
    def _run(self) -> str:
        client = get_shared_mcp_client()
        if not client.sessions:
            return "Error: MCP servers are not connected."

        async def _search():
            results = []
            for srv_name, session in client.sessions.items():
                if self.allowed_servers is not None and srv_name not in self.allowed_servers:
                    continue
                try:
                    prompts_res = await session.list_prompts()
                    for p in prompts_res.prompts:
                        results.append({
                            "server": srv_name,
                            "prompt_name": p.name,
                            "description": p.description or "",
                            "arguments": [arg.name for arg in getattr(p, 'arguments', [])] if hasattr(p, 'arguments') else []
                        })
                except Exception:
                    continue
            if not results:
                return "No MCP prompts found across allowed servers."
            return json.dumps(results, indent=2)

        return _run_async(_search())


# ─────────────────────────────────────────────────────────────────────────────
# CrewAI Tool 4 — Get MCP Prompt
# ─────────────────────────────────────────────────────────────────────────────

class GetMCPPromptSchema(BaseModel):
    server_name: str = Field(..., description="Name of the server providing the prompt.")
    prompt_name: str = Field(..., description="Exact name of the prompt to fetch.")
    arguments: Optional[Dict[str, str]] = Field(None, description="Arguments to pass to the prompt template.")

class GetMCPPrompt(BaseTool):
    name: str = "get_mcp_prompt"
    description: str = "Fetches a specific prompt from a designated MCP server."
    args_schema: Type[BaseModel] = GetMCPPromptSchema
    allowed_servers: Optional[list] = Field(None, exclude=True)

    @safe_tool_run("get_mcp_prompt")
    def _run(self, server_name: str, prompt_name: str, arguments: Optional[Dict[str, str]] = None, **kwargs) -> str:
        if self.allowed_servers is not None and server_name not in self.allowed_servers:
            return f"Error: Access denied. Server '{server_name}' is not allowed."
        client = get_shared_mcp_client()
        async def _get():
            try:
                res = await client.get_prompt(server_name, prompt_name, arguments)
                if hasattr(res, "messages"):
                    return "\\n".join(
                        f"[{m.role}]: {m.content.text if hasattr(m.content, 'text') else str(m.content)}"
                        for m in res.messages
                    )
                return str(res)
            except Exception as e:
                return f"Error fetching prompt '{prompt_name}' from '{server_name}': {e}"
        return _run_async(_get())


# ─────────────────────────────────────────────────────────────────────────────
# CrewAI Tool 5 — Search MCP Resources
# ─────────────────────────────────────────────────────────────────────────────

class SearchMCPResources(BaseTool):
    name: str = "search_mcp_resources"
    description: str = "USE THIS FOR: Discovering available resources (files, URIs, datasets) provided by connected MCP servers."
    allowed_servers: Optional[list] = Field(None, exclude=True)

    @safe_tool_run("search_mcp_resources")
    def _run(self) -> str:
        client = get_shared_mcp_client()
        if not client.sessions:
            return "Error: MCP servers are not connected."

        async def _search():
            results = []
            for srv_name, session in client.sessions.items():
                if self.allowed_servers is not None and srv_name not in self.allowed_servers:
                    continue
                try:
                    res = await session.list_resources()
                    for r in res.resources:
                        results.append({
                            "server": srv_name,
                            "name": r.name,
                            "uri": r.uri,
                            "mimeType": r.mimeType or ""
                        })
                except Exception:
                    continue
            if not results:
                return "No MCP resources found."
            return json.dumps(results, indent=2)
        return _run_async(_search())


# ─────────────────────────────────────────────────────────────────────────────
# CrewAI Tool 6 — Read MCP Resource
# ─────────────────────────────────────────────────────────────────────────────

class ReadMCPResourceSchema(BaseModel):
    server_name: str = Field(..., description="Name of the server providing the resource.")
    uri: str = Field(..., description="Exact URI of the resource to read.")

class ReadMCPResource(BaseTool):
    name: str = "read_mcp_resource"
    description: str = "Reads the contents of a specific resource by its URI from a designated MCP server."
    args_schema: Type[BaseModel] = ReadMCPResourceSchema
    allowed_servers: Optional[list] = Field(None, exclude=True)

    @safe_tool_run("read_mcp_resource")
    def _run(self, server_name: str, uri: str, **kwargs) -> str:
        if self.allowed_servers is not None and server_name not in self.allowed_servers:
            return f"Error: Access denied. Server '{server_name}' is not allowed."
        client = get_shared_mcp_client()
        async def _read():
            try:
                res = await client.read_resource(server_name, uri)
                if hasattr(res, "contents") and res.contents:
                    parts = []
                    for item in res.contents:
                        if hasattr(item, "text") and item.text:
                            parts.append(item.text)
                        else:
                            parts.append(str(item))
                    output = "\\n".join(parts)
                    if len(output) > 10000:
                        output = output[:10000] + "\\n[... Output truncated ...]"
                    return output
                return str(res)
            except Exception as e:
                return f"Error reading resource '{uri}' from '{server_name}': {e}"
        return _run_async(_read())


# ─────────────────────────────────────────────────────────────────────────────
# Concrete Concrete Tools for RFP Agents (Flat Schemas)
# ─────────────────────────────────────────────────────────────────────────────

class ReadDocumentSchema(BaseModel):
    filename: str = Field(..., description="Exact name of the PDF file to read.")

class ReadDocumentTool(BaseTool):
    name: str = "read_document"
    description: str = "Reads and extracts all text from a PDF document. You MUST pass the filename verbatim."
    args_schema: Type[BaseModel] = ReadDocumentSchema
    
    @safe_tool_run("read_document")
    def _run(self, filename: str, **kwargs) -> str:
        client = get_shared_mcp_client()
        async def _execute():
            try:
                res = await client.call_tool("mcp-document-reader", "read_document", {"filename": filename})
                if hasattr(res, "content") and res.content:
                    parts = []
                    for item in res.content:
                        if hasattr(item, "text") and item.text:
                            parts.append(item.text)
                        elif isinstance(item, dict) and "text" in item:
                            parts.append(item["text"])
                        else:
                            parts.append(str(item))
                    output = "\\n".join(parts)
                    return output
                return str(res)
            except Exception as e:
                return f"Error reading document '{filename}': {e}"
        return _run_async(_execute())

class GetDrafterPromptTool(BaseTool):
    name: str = "get_drafter_prompt"
    description: str = "Fetches the drafter prompt rules. Takes no arguments."
    
    @safe_tool_run("get_drafter_prompt")
    def _run(self, **kwargs) -> str:
        client = get_shared_mcp_client()
        async def _execute():
            try:
                res = await client.get_prompt("prompts", "drafter-prompt", {})
                if hasattr(res, "messages") and res.messages:
                    parts = []
                    for msg in res.messages:
                        if hasattr(msg, "content") and hasattr(msg.content, "text"):
                            parts.append(msg.content.text)
                        elif isinstance(msg, dict) and "content" in msg and "text" in msg["content"]:
                            parts.append(msg["content"]["text"])
                        else:
                            parts.append(str(msg))
                    return "\\n".join(parts)
                return str(res)
            except Exception as e:
                return f"Error fetching drafter prompt: {e}"
        return _run_async(_execute())

# ─────────────────────────────────────────────────────────────────────────────
# Public helpers — return tools for injection into BaseAgent
# ─────────────────────────────────────────────────────────────────────────────


def get_scoped_mcp_tools(allowed_servers: Optional[list] = None) -> list:
    """Returns scoped MCP meta-tools for injection into subagents based on allowed_servers."""
    if allowed_servers is not None and len(allowed_servers) == 0:
        return []
    return [
        SearchMCPTools(allowed_servers=allowed_servers),
        CallMCPTool(allowed_servers=allowed_servers),
        SearchMCPPrompts(allowed_servers=allowed_servers),
        GetMCPPrompt(allowed_servers=allowed_servers),
        SearchMCPResources(allowed_servers=allowed_servers),
        ReadMCPResource(allowed_servers=allowed_servers),
        ReadDocumentTool(),
        GetDrafterPromptTool()
    ]
