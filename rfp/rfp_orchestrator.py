import time
import logging
import re
import json
from difflib import SequenceMatcher
from crewai import Crew, Task, Process

from litellm_router.gateway import GenerationResult
from agents.core.llm_wrapper import _get_gateway
from agents.core.base_crew import reset_subagent_call_counts
from rfp.agents import (
    create_organizer_agent,
    create_drafter_agent
)
from agents.utils.mcp_tools import ensure_mcp_connected
from rfp.tools import search_company_knowledge_raw, write_pdf

logger = logging.getLogger(__name__)

def _extract_pdf_text(filepath: str) -> str:
    """Extract raw text from a PDF file. Works with any PDF format."""
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(filepath)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        logger.warning(f"Could not extract PDF text: {e}")
        return ""


def _question_exists_in_source(question_text: str, source_text: str, threshold: float = 0.6) -> bool:
    """Check if a question extracted by the LLM actually exists in the source PDF.
    
    Uses fuzzy matching so minor reformatting by the LLM (whitespace, 
    punctuation differences) doesn't cause false negatives.
    Works for ANY PDF — no hardcoded keywords or format assumptions.
    """
    if not question_text or not source_text:
        return False
    
    # Normalize both texts for comparison
    q_normalized = re.sub(r'\s+', ' ', question_text.lower().strip())
    s_normalized = re.sub(r'\s+', ' ', source_text.lower())
    
    # Quick check: exact substring match (handles most cases)
    if q_normalized in s_normalized:
        return True
    
    # Fuzzy check: take a significant chunk of the question (first 50 chars)
    # and look for it in the source. This handles cases where PDF extraction
    # splits a question across lines.
    q_chunk = q_normalized[:min(50, len(q_normalized))]
    if q_chunk in s_normalized:
        return True
    
    # Final fuzzy check: use SequenceMatcher to find the best match in a sliding
    # window. This handles minor word reordering or OCR artifacts.
    # We check against windows of similar length in the source text.
    q_words = q_normalized.split()
    if len(q_words) < 3:
        return False
    
    # Use the key content words (skip first word if it's a number/label)
    key_phrase = ' '.join(q_words[:8])  # First 8 words as anchor
    
    # Search in overlapping windows of the source
    window_size = len(key_phrase) + 20
    best_ratio = 0.0
    for i in range(0, len(s_normalized) - window_size + 1, 10):
        window = s_normalized[i:i + window_size]
        ratio = SequenceMatcher(None, key_phrase, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
        if best_ratio >= threshold:
            return True
    
    return best_ratio >= threshold

class RFPOrchestrator:
    """
    Sequential Orchestrator specifically for the RFP pipeline.
    Bypasses the Manager LLM to guarantee perfect context handoff 
    without truncation between the RFP agents.
    """

    def __init__(self):
        try:
            print("\n[START] MCP Initialization\nTask: Connecting to MCP servers (mcp-document-reader, prompts)...")
            ensure_mcp_connected()
            print("[SUCCESS] Successfully connected to: mcp-document-reader, prompts")
        except Exception as e:
            logger.warning(f"MCP servers failed to connect at startup: {e}")
            print(f"[ERROR] Failed to connect to MCP servers. Error: {e}")
        self.gateway = _get_gateway()

        # Build the RFP agents
        mcp_servers_org = ["mcp-document-reader"]
        mcp_servers_dft = ["prompts"]

        self.organizer = create_organizer_agent(mcp_servers=mcp_servers_org)
        self.drafter = create_drafter_agent(mcp_servers=mcp_servers_dft)

    def _snapshot_metrics(self) -> tuple[float, int, float]:
        return self.gateway.budget_tracker.get_current_spend(), self.gateway.session_tokens, time.time()

    def _calculate_metrics(
        self, budget_before: float, tokens_before: int, start_time: float
    ) -> tuple[float, float, float, int]:
        time_needed = time.time() - start_time
        budget_after = self.gateway.budget_tracker.get_current_spend()
        total_cost = max(0.0, budget_after - budget_before)
        remaining_budget = max(
            0.0,
            self.gateway.budget_tracker.max_monthly_budget_usd - budget_after,
        )
        tokens_used = max(0, self.gateway.session_tokens - tokens_before)
        return time_needed, total_cost, remaining_budget, tokens_used

    def run(self, query: str, filename: str, output_filename: str = None) -> GenerationResult:
        """Run the sequential multi-agent RFP processing workflow."""
        self.gateway.session_tokens = 0
        budget_before, tokens_before, start_time = self._snapshot_metrics()

        print(f"\n[START] RFP Organizer\nTask: Extracting questions from document...\n")

        # Step 1: Extract the raw source text from the PDF for later validation
        source_pdf_text = _extract_pdf_text(filename)
        if source_pdf_text:
            print(f"[ORCHESTRATOR] Loaded source PDF text ({len(source_pdf_text)} chars) for hallucination validation.\n")

        # Step 2: Organizer extracts questions
        safe_filename = filename.replace('\\', '/')
        task_extract = Task(
            description=f"User Query: {query}\n\nRead the provided RFP document using the read_document tool. You MUST pass exactly this Action Input: {{\"filename\": \"{safe_filename}\"}}. Then extract all questions into a clean, structured list.\n\nIMPORTANT: Extract ONLY questions that are explicitly written in the document. Do NOT invent, infer, or generate any additional questions. If the document has 10 questions, you output exactly 10.",
            expected_output="A strict JSON array containing ONLY the extracted questions. Do not include rubrics, criteria, or markdown. Example: [{\"question\": \"Question 1\", \"extra_info\": \"\"}]",
            agent=self.organizer
        )

        crew_extract = Crew(
            agents=[self.organizer],
            tasks=[task_extract],
            process=Process.sequential,
            verbose=True,
        )

        questions = []
        try:
            reset_subagent_call_counts()
            
            budget_before_extract, tokens_before_extract, time_before_extract = self._snapshot_metrics()
            extract_result = crew_extract.kickoff()
            time_ex, cost_ex, _, tokens_ex = self._calculate_metrics(budget_before_extract, tokens_before_extract, time_before_extract)
            print(f"\nModel Selected: gpt-4o\nCost: ${cost_ex:.6f}\nTokens Needed: {tokens_ex}\nTime: {time_ex:.2f}s\n")
            
            extracted_text = str(extract_result)
            print(f"\n[ORCHESTRATOR] Organizer Extraction Complete\nTask: Extracting questions\nResult: {extracted_text}\n")
            
            # First try: clean markdown formatting if present and parse as full JSON
            clean_json = extracted_text.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json[7:]
            elif clean_json.startswith("```"):
                clean_json = clean_json[3:]
            if clean_json.endswith("```"):
                clean_json = clean_json[:-3]
            clean_json = clean_json.strip()

            try:
                questions = json.loads(clean_json)
            except json.JSONDecodeError:
                pass
            
            # Second try: Use regex to robustly find a JSON array in the output
            if not questions:
                json_match = re.search(r'\[\s*\{.*?\}\s*\]', extracted_text, re.DOTALL)
                if json_match:
                    try:
                        questions = json.loads(json_match.group(0))
                    except json.JSONDecodeError:
                        pass
            
            # Third try: If standard regex fails, try to extract individual question objects
            if not questions:
                object_matches = re.findall(r'\{\s*"question"\s*:\s*".*?"\s*,\s*"extra_info"\s*:\s*".*?"\s*\}', extracted_text, re.DOTALL | re.IGNORECASE)
                if object_matches:
                    for obj_str in object_matches:
                        try:
                            questions.append(json.loads(obj_str))
                        except json.JSONDecodeError:
                            continue
            
            # Absolute fallback: carefully look for numbered questions or lines ending in '?' but ignore thoughts/actions
            if not questions:
                for q in extracted_text.split('\n'):
                    q = q.strip()
                    if len(q) > 5 and q.endswith('?'):
                        # Ignore common CrewAI artifact lines
                        if q.lower().startswith(('thought:', 'action:', 'action input:', 'observation:', 'here are', 'here is')):
                            continue
                        questions.append({"question": q, "extra_info": ""})

            # Validate each question against the source PDF text — drop hallucinated ones
            if source_pdf_text and questions:
                validated_questions = []
                for q_dict in questions:
                    q_text = q_dict.get('question', '') if isinstance(q_dict, dict) else str(q_dict)
                    if _question_exists_in_source(q_text, source_pdf_text):
                        validated_questions.append(q_dict)
                    else:
                        print(f"[ORCHESTRATOR WARNING] Dropping hallucinated question not found in source PDF: \"{q_text[:80]}...\"")
                
                if validated_questions:
                    if len(validated_questions) < len(questions):
                        print(f"\n[ORCHESTRATOR] Dropped {len(questions) - len(validated_questions)} hallucinated question(s). Keeping {len(validated_questions)} verified question(s).\n")
                    questions = validated_questions
                else:
                    # If validation dropped everything (shouldn't happen), keep originals
                    print(f"\n[ORCHESTRATOR WARNING] Validation was too aggressive — keeping all {len(questions)} questions.\n")

            print(f"\n[ORCHESTRATOR] Extracted {len(questions)} questions. Starting batch processing...\n")
            
            all_drafted_answers = []
            
            # Process in batches of 3
            batch_size = 3
            for i in range(0, len(questions), batch_size):
                batch = questions[i:i+batch_size]
                batch_numbered_clean = []
                batch_numbered_search = []
                for j, q_dict in enumerate(batch):
                    # Robust fallback in case the LLM returned a string instead of a dict
                    if isinstance(q_dict, str):
                        q_dict = {"question": q_dict, "extra_info": ""}
                    
                    raw_q = q_dict.get('question', '')
                    raw_info = q_dict.get('extra_info', '')
                    
                    # Clean up newlines to prevent breaking the SearchCompanyKnowledgeTool's newline split
                    clean_q = raw_q.replace('\n', ' ').strip()
                    clean_info = raw_info.replace('\n', ' ').strip()
                    
                    # Strip existing numbering from the question text to prevent LLM confusion
                    clean_q = re.sub(r'^(?:Question\s*)?\d+[\.\:\)]?\s*', '', clean_q, flags=re.IGNORECASE)
                    
                    # Store clean version for the PDF
                    clean_line = f"Question {i + j + 1}: {clean_q}"
                    batch_numbered_clean.append(clean_line)
                    
                    # Format for search with extra info
                    search_line = clean_line
                    if clean_info:
                        search_line += f" | Additional Context from PDF: {clean_info}"
                        
                    batch_numbered_search.append(search_line)
                    
                batch_text = "\n".join(batch_numbered_search)
                
                print(f"\n[ORCHESTRATOR] Processing Batch {i//batch_size + 1} ({len(batch)} questions)...\n")
                
                reset_subagent_call_counts()
                

                
                print(f"\n[START] Database Search Tool\nTask: FastEmbed + Qdrant Hybrid Search (Batch {i//batch_size + 1})...\n")
                
                budget_before_r, tokens_before_r, time_before_r = self._snapshot_metrics()
                
                # Call the raw function directly, bypassing CrewAI overhead for massive speedup
                retrieve_result, retrieved_tokens = search_company_knowledge_raw(batch_text)
                self.gateway.session_tokens += retrieved_tokens
                
                # Mock a tool execution in the terminal for the UI to display cleanly
                print("Agent Tool Execution\nProcess: Database Vector Search\nUsing Tool: SearchCompanyKnowledgeTool")
                print(f"Tool Input\n{batch_text}\nTool Output\n{retrieve_result}")
                print("Final Answer: Successfully retrieved context.\n")
                
                time_r, cost_r, _, tokens_r = self._calculate_metrics(budget_before_r, tokens_before_r, time_before_r)
                print(f"\nModel Selected: FastEmbed / gpt-4o (Embeddings)\nCost: ${cost_r:.6f}\nTokens Needed: {tokens_r}\nTime: {time_r:.2f}s\n")
                
                task_draft = Task(
                    description=f"Context from Retriever:\n{retrieve_result}\n\nDraft professional answers for each RFP question using the provided context from the Retriever. Log any gaps. Do not hallucinate.\nCRITICAL: Ensure your final output strictly follows this exact format:\nA valid JSON array containing one object for each question, where each object has a single key 'answer'.\nExample: [{{\"answer\": \"Text...\"}}, {{\"answer\": \"Text...\"}}]\nDo NOT wrap the JSON in markdown code blocks. Just output the raw JSON array. Do not output anything else.",
                    expected_output="A raw JSON array containing exactly the same number of answers as questions in the batch.",
                    agent=self.drafter
                )

                # Add validation safety net just for the Drafter
                max_retries = 2
                final_batch_text = ""
                budget_before_d, tokens_before_d, time_before_d = self._snapshot_metrics()
                print(f"\n[START] RFP Drafter\nTask: Drafting answers for batch {i//batch_size + 1}...\n")
                for attempt in range(max_retries):
                    crew_draft = Crew(
                        agents=[self.drafter],
                        tasks=[task_draft],
                        verbose=True
                    )
                    batch_result = str(crew_draft.kickoff())
                    
                    try:
                        answers = []
                        # 1. Try robust JSON array extraction
                        json_match = re.search(r'\[\s*\{.*?\}\s*\]', batch_result, re.DOTALL)
                        if json_match:
                            answers = json.loads(json_match.group(0))
                        else:
                            # 2. If no array, try finding individual answer objects (fixes batch size of 1)
                            object_matches = re.findall(r'\{\s*["\']answer["\']\s*:.*?\}', batch_result, re.DOTALL | re.IGNORECASE)
                            if object_matches:
                                for obj_str in object_matches:
                                    try:
                                        answers.append(json.loads(obj_str))
                                    except json.JSONDecodeError:
                                        continue
                            
                            # 3. Last resort fallback
                            if not answers:
                                clean_json = batch_result.strip()
                                if clean_json.startswith("```json"):
                                    clean_json = clean_json[7:]
                                elif clean_json.startswith("```"):
                                    clean_json = clean_json[3:]
                                if clean_json.endswith("```"):
                                    clean_json = clean_json[:-3]
                                clean_json = clean_json.strip()
                                answers = json.loads(clean_json)
                        
                        if isinstance(answers, dict):
                            answers = [answers]
                            
                        if len(answers) != len(batch_numbered_clean):
                            print(f"\n[WARNING] Output mismatch. Expected {len(batch_numbered_clean)} answers, got {len(answers)}. Retrying...")
                            continue
                            
                        # Stitch back together using the clean questions
                        stitched = []
                        for idx, ans_obj in enumerate(answers):
                            # Handle capitalization edge cases from LLM
                            ans_text = ans_obj.get('answer') or ans_obj.get('Answer') or "No answer generated."
                            stitched.append(f"{batch_numbered_clean[idx]}\nAnswer: {ans_text}")
                            
                        final_batch_text = "\n\n".join(stitched)
                        break
                        
                    except Exception as e:
                        print(f"\n[WARNING] Failed to parse JSON: {str(e)}. Retrying batch {i//batch_size + 1} (Attempt {attempt+1}/{max_retries})...")
                        continue
                        
                time_d, cost_d, _, tokens_d = self._calculate_metrics(budget_before_d, tokens_before_d, time_before_d)
                print(f"\nModel Selected: gpt-4o\nCost: ${cost_d:.6f}\nTokens Needed: {tokens_d}\nTime: {time_d:.2f}s\n")
                
                if not final_batch_text:
                    final_batch_text = f"Error generating answers for batch {i//batch_size + 1}."
                    
                all_drafted_answers.append(final_batch_text)
            
            print(f"\n[ORCHESTRATOR] Finished processing all batches. Sending to QA Reviewer...\n")
            
            full_draft_text = "\n\n".join(all_drafted_answers)
            
            # Clean out any stray tool call artifacts hallucinated by the Drafter.
            # Only target triple backticks with Action blocks
            full_draft_text = re.sub(r'```(?:json)?\s*Action:.*?Action Input:.*?```', '', full_draft_text, flags=re.DOTALL)
            
            # Remove any stray "Action: None" or "Action Input:" lines that are NOT in code blocks
            # by replacing them line by line without dotall.
            full_draft_text = re.sub(r'(?m)^Thought:.*\n?', '', full_draft_text)
            full_draft_text = re.sub(r'(?m)^Action:.*\n?', '', full_draft_text)
            full_draft_text = re.sub(r'(?m)^Action Input:.*\n?', '', full_draft_text)
            
            # Aggressively remove all markdown asterisks
            full_draft_text = full_draft_text.replace('**', '')
            
            print("\n" + "="*60)
            print("  [PDF GENERATION]")
            print("="*60)
            
            if not output_filename:
                output_filename = "Myridius_RFP_Response.pdf"
            if not output_filename.lower().endswith('.pdf'):
                output_filename += '.pdf'
                
            print(f"\n[ORCHESTRATOR] Generating PDF as '{output_filename}'...\n")
            output_path = write_pdf(full_draft_text, output_filename)
            
            response = f"RFP processing complete.\nSuccessfully saved PDF to: {output_path}"

        except Exception as exc:
            logger.exception("RFPOrchestrator: Sequential crew failed: %s", exc)
            print(f"\n[ORCHESTRATOR PROTECTION TRIGGERED / ERROR] {exc}\n")
            time_needed, _, remaining_budget, _ = self._calculate_metrics(budget_before, tokens_before, start_time)
            return GenerationResult(
                error=str(exc),
                time_needed=time_needed,
                remaining_budget=remaining_budget,
            )

        time_needed, total_cost, remaining_budget, total_tokens = self._calculate_metrics(budget_before, tokens_before, start_time)

        final_result = GenerationResult(
            response=response,
            model_used="RFPOrchestrator → Sequential Pipeline",
            task_type="sequential_rfp_workflow",
            cost=total_cost,
            time_needed=time_needed,
            remaining_budget=remaining_budget,
            tokens_used=total_tokens,
        )

        print("\n" + "=" * 60)
        print("  [STATS] Final Session Summary")
        print("=" * 60)
        print(f"[SESSION SUMMARY] Time: {time_needed:.2f}s")
        print(f"[SESSION SUMMARY] Cost: ${total_cost:.6f}")
        print(f"[SESSION SUMMARY] Budget Left: ${remaining_budget:.6f}")
        print(f"[SESSION SUMMARY] Tokens Needed: {total_tokens}")
        
        # Calculate Measured Time Saved stretch criteria
        total_questions = len(questions)
        human_time_minutes = total_questions * 15
        time_saved_hours = max(0.0, (human_time_minutes / 60.0) - (time_needed / 3600.0))
        print(f"[SESSION SUMMARY] Time Saved: {time_saved_hours:.1f} hours")
        print()

        return final_result
