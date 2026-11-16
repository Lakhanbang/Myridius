import os
import re
import sys
import litellm
from pydantic import BaseModel, Field, model_validator
from typing import Type
from crewai.tools import BaseTool
from fpdf import FPDF
from dotenv import load_dotenv
from qdrant_client import QdrantClient

# Import Budget Tracker
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    from litellm_router.protections.budget_tracking import BudgetTracker
except ImportError:
    # Fallback if pathing fails
    class BudgetTracker:
        def record_cost(self, cost):
            pass

load_dotenv()

_qdrant_client = None

def get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is None:
        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")
        if not qdrant_url or not qdrant_api_key:
            raise ValueError("QDRANT_URL or QDRANT_API_KEY missing.")
        _qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    return _qdrant_client
_sparse_model = None

def get_sparse_model():
    global _sparse_model
    if _sparse_model is None:
        from fastembed import SparseTextEmbedding
        _sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    return _sparse_model

class SelfHealingBaseModel(BaseModel):
    @model_validator(mode='before')
    @classmethod
    def flatten_dicts_to_strings(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        for key, value in data.items():
            if isinstance(value, dict):
                if "description" in value:
                    data[key] = str(value["description"])
                else:
                    data[key] = str(value)
        return data

class SearchCompanyKnowledgeInput(SelfHealingBaseModel):
    questions: str = Field(
        ...,
        description=(
            "ALL questions to search, separated by newlines. "
            "Pass the COMPLETE list from the Organizer in a single call. "
            "Example: 'What is the uptime SLA? | Additional Context from PDF: 99.9% uptime requirement\\nHow is RBAC structured?'"
        )
    )

def search_company_knowledge_raw(questions: str) -> tuple[str, int]:
    """Raw function to search knowledge base, bypassing CrewAI tool wrapper overhead."""
    try:
        try:
            client = get_qdrant_client()
        except ValueError as e:
            return f"Error: {str(e)}", 0

        # Split the batch into individual questions, skip blank lines
        # Handle both real newlines and escaped \n from LLM agents
        raw_text = questions.strip().replace('\\n', '\n')
        question_list = [q.strip() for q in raw_text.split("\n") if q.strip()]
        if not question_list:
            return "Error: No questions provided. Please pass at least one question.", 0

        all_results = []
        total_embedding_tokens = 0
        try:
            tracker = BudgetTracker()
        except Exception:
            tracker = None

        try:
            # 1. Batch Dense Embeddings
            model_name = "text-embedding-3-small"
            response = litellm.embedding(model=model_name, input=question_list)
            
            # Add usage tokens
            if hasattr(response, "usage") and hasattr(response.usage, "total_tokens"):
                total_embedding_tokens += response.usage.total_tokens
            elif isinstance(response, dict) and "usage" in response:
                total_embedding_tokens += response["usage"].get("total_tokens", 0)
                
            cost = litellm.completion_cost(completion_response=response)

            if tracker:
                try:
                    tracker.record_cost(cost)
                except Exception:
                    pass

            dense_vectors = [data["embedding"] for data in (response.data if hasattr(response, 'data') else response['data'])]

            # 2. Batch Sparse Embeddings
            sparse_model = get_sparse_model()
            sparse_embs = list(sparse_model.embed(question_list))

            from qdrant_client.models import Prefetch, FusionQuery, Fusion, SparseVector, QueryRequest
            
            # 3. Create Qdrant QueryRequests
            requests = []
            for i in range(len(question_list)):
                req = QueryRequest(
                    prefetch=[
                        Prefetch(
                            query=dense_vectors[i],
                            using="dense",
                            limit=15
                        ),
                        Prefetch(
                            query=SparseVector(
                                indices=sparse_embs[i].indices.tolist(),
                                values=sparse_embs[i].values.tolist()
                            ),
                            using="sparse",
                            limit=15
                        )
                    ],
                    query=FusionQuery(fusion=Fusion.RRF),
                    limit=3,
                    with_payload=True
                )
                requests.append(req)

            # 4. Batch Hybrid Search
            batch_results = client.query_batch_points(
                collection_name="company_knowledge",
                requests=requests
            )

            # 5. Format Results
            for i, question in enumerate(question_list):
                hits = batch_results[i].points
                if not hits:
                    all_results.append(f"### Q: {question}\n**RETRIEVED CONTEXT:** NO CONTEXT FOUND\n")
                else:
                    context_parts = []
                    for hit in hits:
                        payload = hit.payload or {}
                        source = payload.get("source", "Unknown")
                        text = payload.get("text", "")
                        context_parts.append(f"  [Source: {source}]\n  {text}")
                    context_block = "\n".join(context_parts)
                    all_results.append(f"### Q: {question}\n**RETRIEVED CONTEXT:**\n{context_block}\n")

        except Exception as e:
            # Fallback if the whole batch fails, record error for each question
            for q in question_list:
                all_results.append(f"### Q: {q}\n**RETRIEVED CONTEXT:** Error retrieving context: {str(e)}\n")

        summary = f"Retrieved context for {len(question_list)} question(s):\n\n" + "\n---\n".join(all_results)
        return summary, total_embedding_tokens

    except Exception as e:
        return f"Error initialising knowledge search: {str(e)}", 0

class SearchCompanyKnowledgeTool(BaseTool):
    name: str = "SearchCompanyKnowledgeTool"
    description: str = (
        "Semantically searches the company's historical RFP responses and documentation for answers. "
        "IMPORTANT: Pass ALL questions together in one call as a newline-separated string. "
        "The tool will search Qdrant for each question internally and return all results at once. "
        "DO NOT call this tool once per question — always pass all questions in a single call."
    )
    args_schema: Type[BaseModel] = SearchCompanyKnowledgeInput

    def _run(self, questions: str) -> str:
        res, _ = search_company_knowledge_raw(questions)
        return res


def write_pdf(content: str, filename: str) -> str:
    try:
        if isinstance(content, dict):
            content = str(content.get("description", content))
        if not content or not isinstance(content, str):
            content = "No content provided."

        # Strip any stray asterisks that slipped through the orchestrator cleaning
        content = content.replace('**', '').replace('*', '')
        
        # Replace common unicode characters with ascii equivalents to prevent FPDF Latin-1 decoding errors
        content = (content.replace('•', '-')
                         .replace('”', '"')
                         .replace('“', '"')
                         .replace('’', "'")
                         .replace('‘', "'")
                         .replace('—', '-')
                         .replace('–', '-'))

        pdf = FPDF()

        # Explicit margins: 20mm left/right, 20mm top, 15mm bottom
        pdf.set_margins(left=20, top=20, right=20)
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # Effective text width = page width minus left and right margins
        effective_width = pdf.w - pdf.l_margin - pdf.r_margin  # ~170mm

        # Add centered title
        pdf.set_font("Helvetica", 'B', 16)
        pdf.cell(effective_width, 10, "RFP Response", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)

        # Encode to latin-1 safely to prevent FPDF character errors
        safe_content = content.encode('latin-1', 'replace').decode('latin-1')

        # Parse content line by line
        lines = safe_content.split('\n')
        for line in lines:
            stripped = line.strip()

            # Blank line = paragraph spacing
            if not stripped:
                pdf.ln(4)
                continue

            # "Question X:" lines → bold (match "Question 1:", "Question 12.", etc.)
            if re.match(r'^question\s+\d+', stripped.lower()):
                pdf.set_font("Helvetica", 'B', 12)
                pdf.multi_cell(w=effective_width, h=8, text=stripped, align='L')
                pdf.ln(2)  # small gap after question

            # "Answer:" lines → normal weight
            elif stripped.lower().startswith("answer:"):
                pdf.set_font("Helvetica", '', 12)
                pdf.multi_cell(w=effective_width, h=8, text=stripped, align='L')
                pdf.ln(4)  # slightly bigger gap after answer

            # List Items → normal weight with indentation
            elif stripped.startswith("- ") or (stripped and stripped[0].isdigit() and ". " in stripped[:4]):
                pdf.set_font("Helvetica", '', 12)
                # Temporarily change left margin for proper wrapping
                old_margin = pdf.l_margin
                pdf.set_left_margin(old_margin + 10)
                pdf.set_x(old_margin + 10)  # Force X position to match new margin
                pdf.multi_cell(w=effective_width - 10, h=8, text=stripped, align='L')
                pdf.set_left_margin(old_margin)
                pdf.set_x(old_margin)  # Reset X position back

            # Everything else → normal
            else:
                pdf.set_font("Helvetica", '', 12)
                pdf.set_x(pdf.l_margin)  # Ensure X is at left margin
                pdf.multi_cell(w=effective_width, h=8, text=stripped, align='L')

        # Save to r&d directory
        rd_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        safe_filename = os.path.basename(filename)
        output_path = os.path.join(rd_dir, safe_filename)

        pdf.output(output_path)
        return output_path
    except Exception as e:
        raise Exception(f"Error writing PDF: {str(e)}")
