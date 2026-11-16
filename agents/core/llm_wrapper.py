"""
llm_wrapper.py

Connects CrewAI agents to the LiteLLM router project via a GatewayAdapter.
"""

import sys
import os
from typing import List, Dict

# Ensure Python can find the litellm_router project
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from crewai import LLM
from litellm_router.gateway import LLMControlGateway
from litellm_router.cli import GatewayCLI

import concurrent.futures
from litellm_router.config import AppConfig

# Singleton gateway instance shared across all agents in a session
_gateway = None

def _get_gateway() -> LLMControlGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMControlGateway()
    return _gateway

class TimeoutLimitError(Exception):
    """Raised when an agent or LLM call exceeds the 90s time limit."""
    pass


class GatewayAdapter(LLM):
    """
    A direct bridge between CrewAI and the LLMControlGateway.
    Every call goes through the gateway for budget tracking, rate limiting,
    and terminal receipt output.
    """
    def __init__(self):
        super().__init__(model="openai/custom")
        self.gateway = _get_gateway()

    def _execute_call(self, messages: List[Dict[str, str]], **kwargs) -> str:
        messages = [dict(m) for m in messages]

        # Replace CrewAI's demanding prompt so the subagent doesn't prematurely output placeholder answers
        if messages:
            content = messages[-1]["content"]
            if "give your best Final Answer, your job depends on it!" in content:
                content = content.replace(
                    "give your best Final Answer, your job depends on it!",
                    "execute your domain/MCP tools ('Action: search_mcp_tools' and 'Action: call_mcp_tool') to retrieve real data! After a tool returns data, output 'Final Answer:' with the result."
                )
            if not any("Observation:" in m.get("content", "") for m in messages):
                if "CRITICAL MANDATORY INSTRUCTION FOR SUBAGENT:" not in content:
                    content += "\n\nCRITICAL MANDATORY INSTRUCTION FOR SUBAGENT: You MUST execute a tool ('Action: search_mcp_tools' or 'Action: call_mcp_tool' or domain tool) to fetch real data right now. After you get the data, you can output 'Final Answer:'.\nThought: "
            messages[-1]["content"] = content

        # Filter kwargs to only pass valid LiteLLM parameters
        allowed_keys = {"stop", "tools", "functions", "temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty"}
        clean_kwargs = {k: v for k, v in kwargs.items() if k in allowed_keys and v is not None}
        if getattr(self, "stop", None) and "stop" not in clean_kwargs:
            clean_kwargs["stop"] = self.stop
        
        for attempt in range(1, 3):
            result = self.gateway.generate_response_from_messages(messages, **clean_kwargs)
            if not result.error:
                break
            print(f"  [LLM Attempt {attempt} Failed] {result.error}")
            if attempt == 1:
                raise RuntimeError(f"Gateway Error: {result.error}")

        raw_text = result.response or ""
        
        # If the model simulated its own Observation right after writing an Action (a common LLM multi-turn hallucination when stop words aren't triggered),
        # truncate immediately before Observation so the tool runner actually executes the tool and injects the real Observation.
        if "Action:" in raw_text and "Observation:" in raw_text:
            print("  [SYSTEM ENFORCEMENT] Intercepted model simulating its own Observation after Action. Truncating response to let tool execute.")
            raw_text = raw_text.split("Observation:")[0].strip()

        # Gateway Enforcement: Universal safety net to prevent subagents from hallucinating or bypassing tools.
        task_text = " ".join([m.get("content", "") for m in messages if m.get("role") in ("user", "system")]).strip()

        # 1. Top-Priority Anti-Hallucination Guard: If subagent outputs Final Answer without successful data after tool error/not found, intercept it!
        tool_obs = [m.get("content", "") for m in messages if "Observation:" in m.get("content", "") and any(err in m.get("content", "") for err in ("Error:", "Missing required parameter", "not found", "was not found"))]
        successful_obs = [m.get("content", "") for m in messages if "Observation:" in m.get("content", "") and not any(err in m.get("content", "") for err in ("Error:", "Missing required parameter", "No MCP tools found", "Found ", "not found", "was not found"))]

        if "Final Answer:" in raw_text and not successful_obs and tool_obs:
            error_msg = tool_obs[-1].replace("Observation:", "").strip()
            print(f"  [SYSTEM ENFORCEMENT] Intercepted hallucinated Final Answer after tool failure/missing resource. Forcing truthful error report.")
            raw_text = f"I cannot provide simulated content because the tool returned an error or missing resource.\nFinal Answer: {error_msg}"

        result.response = raw_text
        print("\n" + "-" * 50)
        print("  [SUBAGENT LLM RECEIPT]")
        print("-" * 50)
        GatewayCLI._render(result)
        return raw_text

    def call(self, messages: List[Dict[str, str]], **kwargs) -> str:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._execute_call, messages, **kwargs)
            try:
                return future.result(timeout=90)
            except concurrent.futures.TimeoutError:
                raise TimeoutLimitError("LLM call exceeded the 90-second timeout limit.")

def get_crewai_llm():
    """
    Returns a GatewayAdapter for use by all subagents.
    Every LLM call flows through the gateway — budget tracked, receipt printed.
    """
    return GatewayAdapter()

