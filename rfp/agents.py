from typing import Optional
from agents.core.base_crew import BaseAgent

def agent_step_callback(step):
    try:
        import sys
        if hasattr(step, 'thought') and step.thought:
            thought = step.thought
            if thought.startswith("Thought:"):
                thought = thought[8:].strip()
            # Prevent multi-line thoughts from triggering 'Final Answer' parser in frontend
            safe_thought = thought.replace('\\n', ' | ')
            sys.stdout.write(f"Thought: {safe_thought}\\n")
        if hasattr(step, 'tool') and step.tool:
            sys.stdout.write(f"Action: {step.tool}\n")
            sys.stdout.write(f"Action Input: {step.tool_input}\n")
        if hasattr(step, 'result') and step.result:
            sys.stdout.write(f"Observation: {step.result}\n")
        elif hasattr(step, 'output') and step.output:
            sys.stdout.write(f"Final Answer: {step.output}\n")
        sys.stdout.flush()
    except Exception:
        pass
def create_organizer_agent(mcp_servers: Optional[list] = None) -> BaseAgent:
    return BaseAgent(
        role="RFP Organizer",
        goal="Read the raw RFP document and extract all questions that need answering in a structured list.",
        backstory=(
            "You are an expert RFP analyzer. You ONLY have access to the 'read_document' tool.\n"
            "Step 1: Read the file using the `read_document` tool. You must pass the filename in your Action Input as instructed.\n"
            "Step 2: Extract every question into a strict JSON array.\n"
            "CRITICAL RULES:\n"
            "- Extract Verbatim: Copy numbered/explicit questions exactly. Do NOT infer, invent, or auto-complete questions.\n"
            "- NEVER INVENT QUESTIONS: You must ONLY extract questions that are explicitly written in the document. Adding even one extra question is a critical failure.\n"
            "- Exclude Rubrics: IGNORE all 'TARGET RETRIEVAL & VERIFICATION CRITERIA' entirely (they are grading rubrics, not questions).\n"
            "- Extract Context: You MUST populate the 'extra_info' field with the section heading or any surrounding text that gives context to the question.\n"
            "Example: [{\"question\": \"What is the SLA?\", \"extra_info\": \"Support and Maintenance Section\"}]"
        ),
        verbose=True,
        step_callback=agent_step_callback,
        allow_delegation=False,
        mcp_servers=mcp_servers,
        allowed_tools=["read_document"],
        max_iter=4
    )



def create_drafter_agent(mcp_servers: Optional[list] = None) -> BaseAgent:
    return BaseAgent(
        role="RFP Drafter",
        goal="Draft professional answers for each RFP question based strictly on the retrieved context, and log any gaps.",
        backstory=(
            "You are a professional proposal writer. You ONLY have access to the 'get_drafter_prompt' tool.\n"
            "Step 1: Fetch rules using `get_drafter_prompt` with no arguments ({}).\n"
            "Step 2: Write clear, concise answers using the provided context.\n"
            "CRITICAL RULES:\n"
            "- If context says 'NO CONTEXT FOUND', draft a placeholder answer. Do NOT hallucinate facts.\n"
            "- You MUST draft a full answer for EVERY question in the batch.\n"
            "- Format: Your FINAL output MUST be a strict JSON array of objects, with each object containing a single 'answer' key. Example: [{\"answer\": \"Text...\"}, {\"answer\": \"Text...\"}]"
        ),
        verbose=True,
        step_callback=agent_step_callback,
        allow_delegation=False,
        mcp_servers=mcp_servers,
        allowed_tools=["get_drafter_prompt"],
        max_iter=4
    )

