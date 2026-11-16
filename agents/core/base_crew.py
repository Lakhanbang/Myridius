"""
base_crew.py
───────────────────────────────────────────────────────────────────────────
Base agent class for the RFP sequential pipeline.

LLM CALL FLOW PER SUBAGENT
━━━━━━━━━━━━━━━━━━━━━━━━━━
  CrewAI Agent.execute_task()
    └─► GatewayAdapter.call(messages)           ← subagent LLM call
          └─► LLMControlGateway.generate_response_from_messages()
                └─► Budget check (Redis)
                └─► EnterpriseRouter → litellm.completion()
                └─► GatewayCLI._render()         ← prints receipt to terminal
          └─► Returns raw text to agent

"""



import concurrent.futures
from typing import Optional
from crewai import Agent
from agents.core.llm_wrapper import get_crewai_llm, TimeoutLimitError
from agents.utils.mcp_tools import get_scoped_mcp_tools

class SubagentCallLimitExceededError(Exception):
    """Raised when a subagent is called more than 3 times in a single query."""
    pass

# Global dictionary tracking how many times each subagent has executed in the active query
_query_subagent_call_counts = {}

def reset_subagent_call_counts():
    """Resets subagent call counter at the beginning of each query."""
    global _query_subagent_call_counts
    _query_subagent_call_counts.clear()

class BaseAgent(Agent):
    """
    A base class for all subagents that automatically wires up the custom gateway LLM,
    sets defaults for verbose/allow_delegation/max_iter, and enforces:
      1. Max 3 invocations per subagent per query.
      2. 90-second timeout per subagent task execution.
    """
    def __init__(self, mcp_servers: Optional[list] = None, **kwargs):
        kwargs.setdefault('llm', get_crewai_llm())
        kwargs.setdefault('allow_delegation', False)
        kwargs.setdefault('verbose', True)
        kwargs.setdefault('max_iter', 10)
        existing_tools = list(kwargs.pop('tools', []))
        if mcp_servers is None:
            mcp_servers = kwargs.pop('mcp_servers', None)
        if mcp_servers and len(mcp_servers) > 0:
            existing_tools.extend(get_scoped_mcp_tools(allowed_servers=mcp_servers))
        
        allowed_tools = kwargs.pop('allowed_tools', None)
        if allowed_tools is not None:
            existing_tools = [t for t in existing_tools if t.name in allowed_tools]
            
        kwargs['tools'] = existing_tools

        base_backstory = kwargs.get('backstory', '')
        anti_hallucination_guardrail = (
            "\n\nCRITICAL DATA PASSTHROUGH & ANTI-HALLUCINATION LAWS:\n"
            "1. VERBATIM OUTPUT LAW: When returning lists of files, directories, URLs, or database items from any retrieval tool (`list_directory`, `search_mcp_tools`, `call_mcp_tool`, `scrape_tool`), your `Final Answer` MUST exactly mirror the verified items returned by the tool. DO NOT add, guess, or auto-complete unverified filenames (such as `server.py` or `tests/`).\n"
            "2. TRANSPARENT SELF-HEALING LAW: If any tool execution returns an error (such as `Error: File not found`, `404`, or `Access denied`), YOU MUST NOT guess, fabricate, or hallucinate the content of that item. Immediately skip/drop the errored item, continue processing all remaining valid items, and explicitly report in your Final Answer:\n"
            "`Processed verified items: [...] | Skipped unverified/missing items: ['<item>' - <error reason>]`."
        )
        kwargs['backstory'] = (base_backstory + anti_hallucination_guardrail).strip()
        super().__init__(**kwargs)

    def execute_task(self, task, context=None, tools=None):
        global _query_subagent_call_counts
        current_count = _query_subagent_call_counts.get(self.role, 0)
        if current_count >= 3:
            raise SubagentCallLimitExceededError(
                f"MANAGER STOP INSTRUCTION: Subagent '{self.role}' has been called 3 times and is now BANNED for this query. "
                f"DO NOT delegate to '{self.role}' again. "
                f"You MUST immediately output 'Final Answer:' with whatever results have been collected so far."
            )
        _query_subagent_call_counts[self.role] = current_count + 1

        def _run_task():
            return super(BaseAgent, self).execute_task(task, context, tools)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_task)
            try:
                return future.result(timeout=300)
            except concurrent.futures.TimeoutError:
                raise TimeoutLimitError(
                    f"Subagent '{self.role}' exceeded the 300-second timeout limit."
                )
